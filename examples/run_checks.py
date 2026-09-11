#!/usr/bin/env python3
"""Lightweight regression runner for repaired top-level examples.

Default mode is **static**: keyless compile / typecheck / unit tests only.
It never mutates live Zep state.

Default also assumes dependencies are **already installed** in the environment.
Pass ``--install`` (for example ``--install --mode static``) to run the
structured install phase first — that combination is the clean-environment /
full static gate. Install steps are optional so the default path stays lightweight.

Live mode (``--live`` / ``--mode live``) additionally runs explicit smoke commands
only when the required API keys are present in the environment.

Toolchain expectations (explicit):
  - Python 3.10+ (3.12 used in CI/dev images)
  - Node.js 18+ for most TypeScript examples; **Node.js 24+ for eve**
  - npm (and yarn for zep-graph-visualization)
  - Go 1.22+
  - ``uv`` only if you use ``--install`` for python/openai-agents-sdk

Usage (from repo root):
  python3 examples/run_checks.py
  python3 examples/run_checks.py --mode static
  python3 examples/run_checks.py --install --mode static
  python3 examples/run_checks.py --live
  python3 examples/run_checks.py --mode live --dry-run-plan
  python3 examples/run_checks.py --install --dry-run-plan
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

EXAMPLES_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_ROOT.parent
DEFAULT_MODE = "static"

#: Stand-in for the interpreter chosen at run time (see --python).
PYTHON_PLACEHOLDER = "{python}"

#: Examples target Python 3.10+; older interpreters cannot parse match statements.
MIN_PYTHON_VERSION = (3, 10)

#: compileall must skip virtualenvs and vendored trees checked out beside the examples.
COMPILEALL_EXCLUDE = r"(^|/)(\.?venv[^/]*|\.tox|\.nox|node_modules|site-packages|dist|build)(/|$)"

DEFAULT_DEPENDENCY_ASSUMPTION = (
    "Default static/live runs assume existing example dependencies are already installed; "
    "use --install (e.g. --install --mode static) for a clean-environment gate."
)

# Keys that are intentionally out of automated live coverage.
NOT_LIVE_COVERED: dict[str, str] = {
    "TAVILY_API_KEY": (
        "TypeScript langgraph web-search tool only; help/non-search paths work without it. "
        "Not exercised by this runner's live smokes."
    ),
    "PROXY_API_KEY / VITE_ELEVENLABS_AGENT_ID": (
        "python/elevenlabs-zep-example llm-proxy auth (PROXY_API_KEY) and react-app agent id "
        "(VITE_ELEVENLABS_AGENT_ID). Static syntax/import checks only — not live-covered here."
    ),
}


@dataclass(frozen=True)
class InstallStep:
    """One executable install command tied to an on-disk manifest."""

    name: str
    argv: list[str]
    cwd: str  # relative to examples/
    manifest: str  # relative to examples/; must exist


@dataclass(frozen=True)
class Check:
    name: str
    argv: list[str]
    cwd: str  # relative to examples/
    required_keys: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 600


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    rel_path: str
    language: str
    install: tuple[InstallStep, ...]
    run: list[str]
    static_test: list[str]
    required_keys_docs: tuple[str, ...]
    static: tuple[Check, ...]
    live: tuple[Check, ...] = ()
    not_live_covered: bool = False
    notes: str = ""
    install_notes: str = ""


@dataclass
class Plan:
    install_steps: list[InstallStep]
    static_checks: list[Check]
    live_checks: list[Check]
    skipped_live: list[Check]


def make_run_prefix() -> str:
    return f"exrun-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def default_mutates_zep() -> bool:
    return DEFAULT_MODE != "static"


def _py(*args: str) -> list[str]:
    return [PYTHON_PLACEHOLDER, *args]


def _compileall(cwd: str) -> Check:
    return _check(
        "compileall",
        _py("-m", "compileall", "-q", "-x", COMPILEALL_EXCLUDE, "."),
        cwd,
    )


def resolve_python_argv(argv: Sequence[str], python: str) -> list[str]:
    """Replace the interpreter placeholder with the selected interpreter."""
    return [python if arg == PYTHON_PLACEHOLDER else arg for arg in argv]


def check_python_version(
    version: tuple[int, ...],
    min_version: tuple[int, int] = MIN_PYTHON_VERSION,
) -> tuple[bool, str]:
    """Validate an interpreter version, returning (ok, actionable message)."""
    if tuple(version[: len(min_version)]) >= min_version:
        return True, ""
    found = ".".join(str(part) for part in version)
    want = ".".join(str(part) for part in min_version)
    return False, (
        f"Python {want}+ is required to check these examples, but the selected "
        f"interpreter is {found}.\n"
        f"Create a virtualenv on a newer Python and re-run, for example:\n"
        f"  python3.12 -m venv .venv && source .venv/bin/activate\n"
        f"  python3 examples/run_checks.py --install --mode static\n"
        f"Or point the runner at another interpreter:\n"
        f"  python3 examples/run_checks.py --python /path/to/python3.12"
    )


def interpreter_version(python: str) -> tuple[int, ...] | None:
    """Return the version tuple for an interpreter, or None if unusable."""
    if python == sys.executable:
        return sys.version_info[:3]
    try:
        proc = subprocess.run(
            [python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return tuple(int(part) for part in proc.stdout.strip().split("."))
    except ValueError:
        return None


def _check(
    name: str,
    argv: Sequence[str],
    cwd: str,
    required_keys: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    timeout_sec: int = 600,
) -> Check:
    return Check(
        name=name,
        argv=list(argv),
        cwd=cwd,
        required_keys=tuple(required_keys),
        env=dict(env or {}),
        timeout_sec=timeout_sec,
    )


def _install(
    name: str,
    argv: Sequence[str],
    cwd: str,
    manifest: str,
) -> InstallStep:
    return InstallStep(name=name, argv=list(argv), cwd=cwd, manifest=manifest)


INVENTORY: tuple[Group, ...] = (

    Group(
        id='python-root',
        title='Python root scripts (simple / advanced / user_example)',
        rel_path='python',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python', 'python/requirements.txt'),
        ),
        run=['python simple.py', 'python advanced.py', 'python user_example.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .', 'python -m pytest tests/test_v3_regression.py -q'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _compileall('python'),
            _check(
                'v3-regression',
                _py('-m', 'pytest', 'tests/test_v3_regression.py', '-q'),
                'python',
                timeout_sec=300,
            ),
        ),
        live=(
            _check(
                'simple.py',
                _py('simple.py'),
                'python',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='Live scripts mint UUID-based user/thread IDs (not ZEP_EXAMPLE_* env consumption).',
    ),
    Group(
        id='python-graph_example',
        title='Python graph_example',
        rel_path='python/graph_example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', '../requirements.txt'], 'python/graph_example', 'python/requirements.txt'),
        ),
        run=['python graph_example.py', 'python user_graph_example.py', 'python entity_types.py', 'python tickets_example.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _compileall('python/graph_example'),
        ),
        live=(
            _check(
                'graph_example.py',
                _py('graph_example.py'),
                'python/graph_example',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='Live scripts mint UUID-based identifiers.',
    ),
    Group(
        id='python-chat_history',
        title='Python chat_history',
        rel_path='python/chat_history',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', '../requirements.txt'], 'python/chat_history', 'python/requirements.txt'),
        ),
        run=['python memory.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _compileall('python/chat_history'),
        ),
        live=(
            _check(
                'memory.py',
                _py('memory.py'),
                'python/chat_history',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='Live scripts mint UUID-based identifiers.',
    ),
    Group(
        id='python-chunking-example',
        title='Python chunking-example',
        rel_path='python/chunking-example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/chunking-example', 'python/chunking-example/requirements.txt'),
        ),
        run=['python chunk_and_ingest.py sample_document.txt --user-id <id>', 'python chunk_and_ingest.py sample_document.txt --user-id <id> --dry-run'],
        static_test=['python chunk_and_ingest.py --help'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _check(
                'cli-help',
                _py('chunk_and_ingest.py', '--help'),
                'python/chunking-example',
            ),
        ),
        live=(
            _check(
                'chunk-dry-run',
                _py('chunk_and_ingest.py', 'sample_document.txt', '--user-id', '{prefix}-py-chunk', '--dry-run'),
                'python/chunking-example',
                required_keys=('OPENAI_API_KEY',),
            ),
            _check(
                'chunk-ingest',
                _py('chunk_and_ingest.py', 'sample_document.txt', '--user-id', '{prefix}-py-chunk-live'),
                'python/chunking-example',
                required_keys=('ZEP_API_KEY', 'OPENAI_API_KEY'),
            ),
        ),
        notes='OPENAI_API_KEY is required even for --dry-run (contextualization).',
    ),
    Group(
        id='python-claude-prompt-caching-example',
        title='Python claude-prompt-caching-example',
        rel_path='python/claude-prompt-caching-example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/claude-prompt-caching-example', 'python/claude-prompt-caching-example/requirements.txt'),
        ),
        run=['python ingest.py', 'python chat.py'],
        static_test=['python test_structure.py'],
        required_keys_docs=('ZEP_API_KEY', 'ANTHROPIC_API_KEY'),
        static=(
            _check(
                'structure',
                _py('test_structure.py'),
                'python/claude-prompt-caching-example',
            ),
        ),
        live=(
            _check(
                'ingest.py',
                _py('ingest.py'),
                'python/claude-prompt-caching-example',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='Full chat/benchmark needs ANTHROPIC_API_KEY; default live smoke is ingest only. Ingest honors ZEP_EXAMPLE_USER_PREFIX so live runs do not delete the fixed dana demo user.',
    ),
    Group(
        id='python-openai-agents-sdk',
        title='Python openai-agents-sdk',
        rel_path='python/openai-agents-sdk',
        language='python',
        install=(
            _install('uv-sync', ['uv', 'sync'], 'python/openai-agents-sdk', 'python/openai-agents-sdk/pyproject.toml'),
        ),
        run=['python openai_agents_sdk_example.py', 'python openai_agents_sdk_example.py --interactive'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _compileall('python/openai-agents-sdk'),
        ),
        live=(
            _check(
                'one-shot',
                _py('openai_agents_sdk_example.py', '--username', '{prefix}-oai', '--session', '{prefix}-oai-session'),
                'python/openai-agents-sdk',
                required_keys=('ZEP_API_KEY', 'OPENAI_API_KEY'),
            ),
        ),
        notes='Install deps (`uv sync`; alternative: `pip install -e .`) before live runs. Avoid --interactive in automation.',
        install_notes='Alternative without uv: pip install -e .',
    ),
    Group(
        id='python-agent-memory-full-example',
        title='Python agent-memory-full-example (Streamlit)',
        rel_path='python/agent-memory-full-example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/agent-memory-full-example', 'python/agent-memory-full-example/requirements.txt'),
        ),
        run=['streamlit run ui.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _compileall('python/agent-memory-full-example'),
        ),
        notes='Interactive Streamlit UI excluded from default static/live runner.',
    ),
    Group(
        id='python-context-templates-example',
        title='Python context-templates-example (Streamlit)',
        rel_path='python/context-templates-example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/context-templates-example', 'python/context-templates-example/requirements.txt'),
        ),
        run=['python set-context-templates.py', 'python zep_ingest.py', 'streamlit run ui.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _compileall('python/context-templates-example'),
        ),
        live=(
            _check(
                'set-context-templates.py',
                _py('set-context-templates.py'),
                'python/context-templates-example',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='streamlit run ui.py is interactive — not in the default gate.',
    ),
    Group(
        id='python-user-summary-instructions-example',
        title='Python user-summary-instructions-example (Streamlit)',
        rel_path='python/user-summary-instructions-example',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/user-summary-instructions-example', 'python/user-summary-instructions-example/requirements.txt'),
        ),
        run=['python zep_ingest.py', 'streamlit run ui.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _compileall('python/user-summary-instructions-example'),
        ),
        notes='streamlit run ui.py is interactive — not in the default gate.',
    ),
    Group(
        id='python-zep-quickstart-dashboard',
        title='Python zep-quickstart-dashboard (Streamlit)',
        rel_path='python/zep-quickstart-dashboard',
        language='python',
        install=(
            _install('pip-requirements', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/zep-quickstart-dashboard', 'python/zep-quickstart-dashboard/requirements.txt'),
        ),
        run=['python zep_ingest.py', 'streamlit run ui.py'],
        static_test=['python -m compileall -q -x <venv-exclude> .'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _compileall('python/zep-quickstart-dashboard'),
        ),
        notes='streamlit run ui.py is interactive — not in the default gate.',
    ),
    Group(
        id='python-elevenlabs-zep-example',
        title='Python elevenlabs-zep-example (NOT live-covered)',
        rel_path='python/elevenlabs-zep-example',
        language='python',
        install=(
            _install('pip-llm-proxy', [PYTHON_PLACEHOLDER, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'python/elevenlabs-zep-example/llm-proxy', 'python/elevenlabs-zep-example/llm-proxy/requirements.txt'),
            _install('npm-react-app', ['npm', 'install'], 'python/elevenlabs-zep-example/react-app', 'python/elevenlabs-zep-example/react-app/package.json'),
        ),
        run=['cd llm-proxy && python proxy_server.py', 'cd react-app && npm run dev'],
        static_test=['python -m py_compile llm-proxy/proxy_server.py', 'python -m py_compile llm-proxy/setup_test_user.py'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY', 'PROXY_API_KEY', 'VITE_ELEVENLABS_AGENT_ID'),
        static=(
            _check(
                'py_compile_proxy',
                _py('-m', 'py_compile', 'llm-proxy/proxy_server.py'),
                'python/elevenlabs-zep-example',
            ),
            _check(
                'py_compile_setup',
                _py('-m', 'py_compile', 'llm-proxy/setup_test_user.py'),
                'python/elevenlabs-zep-example',
            ),
        ),
        not_live_covered=True,
        notes='Not live-covered. Needs PROXY_API_KEY for the llm-proxy and VITE_ELEVENLABS_AGENT_ID for the react-app, in addition to ZEP_API_KEY and OPENAI_API_KEY.',
    ),
    Group(
        id='typescript-root',
        title='TypeScript graph / memory / users snippets',
        rel_path='typescript',
        language='typescript',
        install=(
            _install('npm-install', ['npm', 'install'], 'typescript', 'typescript/package.json'),
        ),
        run=['npm run example:graph', 'npm run example:graph:user', 'npm run example:graph:entity-types', 'npm run example:memory', 'npm run example:users'],
        static_test=['npm test'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _check(
                'npm-test',
                ['npm', 'test'],
                'typescript',
                timeout_sec=180,
            ),
        ),
        live=(
            _check(
                'example:users',
                ['npm', 'run', 'example:users'],
                'typescript',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='Packaged apps (eve, langgraph, chunking-example, zep-graph-visualization) are separate groups. Live example:users mints UUID user IDs.',
    ),
    Group(
        id='typescript-chunking-example',
        title='TypeScript chunking-example',
        rel_path='typescript/chunking-example',
        language='typescript',
        install=(
            _install('npm-install', ['npm', 'install'], 'typescript/chunking-example', 'typescript/chunking-example/package.json'),
        ),
        run=['npx tsx src/index.ts sample_document.txt --user-id <id>', 'npx tsx src/index.ts sample_document.txt --user-id <id> --dry-run'],
        static_test=['npm test'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _check(
                'npm-test',
                ['npm', 'test'],
                'typescript/chunking-example',
            ),
        ),
        live=(
            _check(
                'chunk-dry-run',
                ['npx', 'tsx', 'src/index.ts', 'sample_document.txt', '--user-id', '{prefix}-ts-chunk', '--dry-run'],
                'typescript/chunking-example',
                required_keys=('OPENAI_API_KEY',),
            ),
        ),
        notes='OPENAI_API_KEY required for --dry-run contextualization.',
    ),
    Group(
        id='typescript-langgraph',
        title='TypeScript langgraph CLI agent',
        rel_path='typescript/langgraph',
        language='typescript',
        install=(
            _install('npm-install', ['npm', 'install'], 'typescript/langgraph', 'typescript/langgraph/package.json'),
        ),
        run=['npm start -- --userId <id> --threadId <id>'],
        static_test=['npm test', 'npx tsx agent.ts --help'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _check(
                'npm-test',
                ['npm', 'test'],
                'typescript/langgraph',
            ),
            _check(
                'cli-help',
                ['npx', 'tsx', 'agent.ts', '--help'],
                'typescript/langgraph',
            ),
        ),
        notes='TAVILY_API_KEY is optional for web search and is NOT live-covered. `npm start` is an interactive readline agent — run manually with unique --userId/--threadId prefixes; not auto-started by this runner.',
    ),
    Group(
        id='typescript-eve',
        title='TypeScript eve (Node 24+)',
        rel_path='typescript/eve',
        language='typescript',
        install=(
            _install('npm-install', ['npm', 'install'], 'typescript/eve', 'typescript/eve/package.json'),
        ),
        run=['npm run smoke', 'npm run seed:company', 'npm run dev'],
        static_test=['npm run typecheck'],
        required_keys_docs=('ZEP_API_KEY', 'GOOGLE_API_KEY'),
        static=(
            _check(
                'typecheck',
                ['npm', 'run', 'typecheck'],
                'typescript/eve',
                timeout_sec=180,
            ),
        ),
        live=(
            _check(
                'smoke',
                ['npm', 'run', 'smoke'],
                'typescript/eve',
                required_keys=('ZEP_API_KEY',),
                env={'ZEP_DEMO_USER_ID': '{prefix}-eve-user', 'ZEP_COMPANY_GRAPH_ID': '{prefix}-eve-company'},
                timeout_sec=240,
            ),
        ),
        notes='Requires Node.js 24+. `npm run smoke` is Zep-only and uses ZEP_DEMO_USER_ID / ZEP_COMPANY_GRAPH_ID from the runner prefix. `npm run dev` is an interactive Eve server — not in the default gate. GOOGLE_API_KEY is required for the Gemini-backed agent UI.',
        install_notes='Requires Node.js 24+.',
    ),
    Group(
        id='typescript-zep-graph-visualization',
        title='TypeScript zep-graph-visualization',
        rel_path='typescript/zep-graph-visualization',
        language='typescript',
        install=(
            _install('yarn-install', ['yarn', 'install'], 'typescript/zep-graph-visualization', 'typescript/zep-graph-visualization/package.json'),
        ),
        run=['yarn dev'],
        static_test=['yarn build'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _check(
                'yarn-build',
                ['yarn', 'build'],
                'typescript/zep-graph-visualization',
                timeout_sec=300,
            ),
        ),
        notes='yarn dev / yarn start are long-lived servers — excluded from default gate.',
    ),
    Group(
        id='go-root',
        title='Go graph / ontology snippets',
        rel_path='go',
        language='go',
        install=(
            _install('go-mod-download', ['go', 'mod', 'download'], 'go', 'go/go.mod'),
        ),
        run=['go run .', 'go run . entity-types'],
        static_test=['go test ./...'],
        required_keys_docs=('ZEP_API_KEY',),
        static=(
            _check(
                'go-test',
                ['go', 'test', './...'],
                'go',
            ),
        ),
        live=(
            _check(
                'user-graph',
                ['go', 'run', '.'],
                'go',
                required_keys=('ZEP_API_KEY',),
            ),
        ),
        notes='entity-types replaces project-level ontology — use a disposable project key. Live user-graph mints UUID IDs.',
    ),
    Group(
        id='go-chunking-example',
        title='Go chunking-example',
        rel_path='go/chunking-example',
        language='go',
        install=(
            _install('go-mod-download', ['go', 'mod', 'download'], 'go/chunking-example', 'go/chunking-example/go.mod'),
        ),
        run=['go run . sample_document.txt --user-id <id>', 'go run . sample_document.txt --user-id <id> --dry-run'],
        static_test=['go test ./...'],
        required_keys_docs=('ZEP_API_KEY', 'OPENAI_API_KEY'),
        static=(
            _check(
                'go-test',
                ['go', 'test', './...'],
                'go/chunking-example',
            ),
        ),
        live=(
            _check(
                'chunk-dry-run',
                ['go', 'run', '.', 'sample_document.txt', '--user-id', '{prefix}-go-chunk', '--dry-run'],
                'go/chunking-example',
                required_keys=('OPENAI_API_KEY',),
            ),
        ),
        notes='OPENAI_API_KEY required for --dry-run contextualization.',
    ),

)


def _keys_available(required: Sequence[str], env: Mapping[str, str]) -> bool:
    return all(bool(str(env.get(k, "")).strip()) for k in required)


def _materialize(check: Check, run_prefix: str) -> Check:
    def expand(value: str) -> str:
        return value.replace("{prefix}", run_prefix)

    argv = [expand(a) for a in check.argv]
    env = {k: expand(v) for k, v in check.env.items()}
    env.setdefault("ZEP_EXAMPLE_RUN_PREFIX", run_prefix)
    env.setdefault("ZEP_EXAMPLE_USER_PREFIX", f"{run_prefix}-user")
    env.setdefault("ZEP_EXAMPLE_THREAD_PREFIX", f"{run_prefix}-thread")
    return Check(
        name=check.name,
        argv=argv,
        cwd=check.cwd,
        required_keys=check.required_keys,
        env=env,
        timeout_sec=check.timeout_sec,
    )


def _dedupe_install_steps(steps: Sequence[InstallStep]) -> list[InstallStep]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[InstallStep] = []
    for step in steps:
        key = (step.manifest, tuple(step.argv))
        if key in seen:
            continue
        seen.add(key)
        out.append(step)
    return out


def build_plan(
    mode: str,
    env: Mapping[str, str] | None = None,
    run_prefix: str | None = None,
    groups: Sequence[Group] | None = None,
    include_install: bool = False,
) -> Plan:
    if mode not in {"static", "live"}:
        raise ValueError(f"unknown mode: {mode}")
    env = dict(env if env is not None else os.environ)
    prefix = run_prefix or make_run_prefix()
    selected_groups = tuple(groups) if groups is not None else INVENTORY

    install_steps: list[InstallStep] = []
    if include_install:
        collected: list[InstallStep] = []
        for group in selected_groups:
            collected.extend(group.install)
        install_steps = _dedupe_install_steps(collected)

    static_checks: list[Check] = []
    for group in selected_groups:
        static_checks.extend(group.static)

    live_checks: list[Check] = []
    skipped_live: list[Check] = []
    if mode == "live":
        for group in selected_groups:
            for check in group.live:
                materialized = _materialize(check, prefix)
                if _keys_available(materialized.required_keys, env):
                    live_checks.append(materialized)
                else:
                    skipped_live.append(materialized)

    return Plan(
        install_steps=install_steps,
        static_checks=static_checks,
        live_checks=live_checks,
        skipped_live=skipped_live,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run static (default) or credential-gated live checks for examples/. "
            + DEFAULT_DEPENDENCY_ASSUMPTION
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("static", "live"),
        default=DEFAULT_MODE,
        help="static = keyless compile/typecheck/tests (default). "
        "live = also run explicit smokes when keys are present.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Shortcut for --mode live",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Run structured install steps before checks. "
        "Default assumes deps already exist; use with --mode static for a clean-env gate.",
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help="Limit to one or more inventory group ids (repeatable)",
    )
    parser.add_argument(
        "--dry-run-plan",
        action="store_true",
        help="Print the planned checks without executing them",
    )
    parser.add_argument(
        "--run-prefix",
        default=None,
        help="Unique user/thread id prefix for live runs (default: generated exrun-...)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter used for Python checks and pip installs "
        f"(default: the launching interpreter). Requires Python "
        f"{'.'.join(str(p) for p in MIN_PYTHON_VERSION)}+.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.live:
        args.mode = "live"
    return args


def _format_check(check: Check, python: str = PYTHON_PLACEHOLDER) -> str:
    cmd = " ".join(shlex.quote(a) for a in resolve_python_argv(check.argv, python))
    keys = ",".join(check.required_keys) if check.required_keys else "-"
    return f"[{check.cwd}] {cmd} (keys={keys})"


def _format_install(step: InstallStep, python: str = PYTHON_PLACEHOLDER) -> str:
    cmd = " ".join(shlex.quote(a) for a in resolve_python_argv(step.argv, python))
    return f"[{step.cwd}] {cmd} (manifest={step.manifest})"


def run_check(check: Check, python: str = sys.executable) -> int:
    cwd = EXAMPLES_ROOT / check.cwd
    env = os.environ.copy()
    env.update(check.env)
    argv = resolve_python_argv(check.argv, python)
    print(f"→ {_format_check(check)}", flush=True)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            timeout=check.timeout_sec,
            check=False,
        )
    except FileNotFoundError as exc:
        print(f"✗ missing executable: {exc}", flush=True)
        return 127
    except subprocess.TimeoutExpired:
        print(f"✗ timeout after {check.timeout_sec}s", flush=True)
        return 124
    if proc.returncode != 0:
        print(f"✗ exit {proc.returncode}", flush=True)
    else:
        print("✓ ok", flush=True)
    return int(proc.returncode)


def run_install(step: InstallStep, python: str = sys.executable) -> int:
    cwd = EXAMPLES_ROOT / step.cwd
    argv = resolve_python_argv(step.argv, python)
    print(f"→ install {_format_install(step)}", flush=True)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=os.environ.copy(),
            timeout=1200,
            check=False,
        )
    except FileNotFoundError as exc:
        print(f"✗ missing executable: {exc}", flush=True)
        return 127
    except subprocess.TimeoutExpired:
        print("✗ install timeout", flush=True)
        return 124
    if proc.returncode != 0:
        print(f"✗ exit {proc.returncode}", flush=True)
    else:
        print("✓ ok", flush=True)
    return int(proc.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    groups: Sequence[Group] = INVENTORY
    if args.group:
        wanted = set(args.group)
        groups = tuple(g for g in INVENTORY if g.id in wanted)
        missing = wanted - {g.id for g in groups}
        if missing:
            print(f"Unknown group id(s): {sorted(missing)}", file=sys.stderr)
            return 2

    python = args.python
    version = interpreter_version(python)
    if version is None:
        print(f"Cannot run the selected interpreter: {python}", file=sys.stderr)
        return 2
    ok, message = check_python_version(version)
    if not ok:
        print(message, file=sys.stderr)
        return 2

    prefix = args.run_prefix or make_run_prefix()
    plan = build_plan(
        mode=args.mode,
        env=os.environ,
        run_prefix=prefix,
        groups=groups,
        include_install=args.install,
    )

    print(
        f"examples runner  mode={args.mode}  install={args.install}  prefix={prefix}\n"
        f"python: {python} ({'.'.join(str(p) for p in version)})"
    )
    print(
        f"install steps: {len(plan.install_steps)}  "
        f"static checks: {len(plan.static_checks)}  "
        f"live checks: {len(plan.live_checks)}  "
        f"skipped live: {len(plan.skipped_live)}"
    )
    if not args.install:
        print(f"note: {DEFAULT_DEPENDENCY_ASSUMPTION}")
    if plan.skipped_live:
        for skipped in plan.skipped_live:
            missing = [k for k in skipped.required_keys if not str(os.environ.get(k, "")).strip()]
            print(f"  skip live {skipped.name}: missing {', '.join(missing)}")

    if args.dry_run_plan:
        print("\nInstall:")
        for step in plan.install_steps:
            print(" ", _format_install(step, python))
        print("\nStatic:")
        for check in plan.static_checks:
            print(" ", _format_check(check, python))
        print("\nLive:")
        for check in plan.live_checks:
            print(" ", _format_check(check, python))
        return 0

    failures = 0
    for step in plan.install_steps:
        rc = run_install(step, python)
        if rc != 0:
            failures += 1
    for check in plan.static_checks:
        rc = run_check(check, python)
        if rc != 0:
            failures += 1
    for check in plan.live_checks:
        rc = run_check(check, python)
        if rc != 0:
            failures += 1

    if failures:
        print(f"\nFAILED: {failures} check(s)", flush=True)
        return 1
    print("\nAll scheduled checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
