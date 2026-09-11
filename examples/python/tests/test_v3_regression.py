"""
Offline regression harness for examples/python vs zep-cloud v3.28.

Captures known failures without calling live Zep/OpenAI services.

Run from repo root:
  python -m pytest examples/python/tests/test_v3_regression.py -q
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[1]

STREAMLIT_UIS = [
    PYTHON_ROOT / "agent-memory-full-example" / "ui.py",
    PYTHON_ROOT / "context-templates-example" / "ui.py",
    PYTHON_ROOT / "user-summary-instructions-example" / "ui.py",
    PYTHON_ROOT / "zep-quickstart-dashboard" / "ui.py",
]

NOTEBOOKS = [
    PYTHON_ROOT / "quickstart" / "quickstart.ipynb",
    PYTHON_ROOT / "autogen-agent" / "agent.ipynb",
    PYTHON_ROOT / "langgraph-agent" / "agent.ipynb",
]

STALE_NOTEBOOK_PATTERNS = [
    re.compile(r"\bmemory\.add_session\b"),
    re.compile(r"\.memory\.add_session\b"),
    re.compile(r"\bzep\.memory\b"),
    re.compile(r"await zep\.memory\."),
    re.compile(r"\bFactRating(Examples|Instruction)\b"),
    re.compile(r"\brole_type\s*="),
]


def _notebook_code(path: Path) -> str:
    nb = json.loads(path.read_text(encoding="utf-8"))
    parts: list[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            parts.append("".join(cell.get("source", [])))
    return "\n".join(parts)


def _zep_requirement_pins() -> dict[str, str]:
    found: dict[str, str] = {}
    files = [PYTHON_ROOT / "requirements.txt", *sorted(PYTHON_ROOT.glob("*/requirements.txt"))]
    for path in files:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s*zep-cloud\s*([^\n#]*)", text)
        found[str(path.relative_to(REPO_ROOT))] = (
            f"zep-cloud{match.group(1).strip()}" if match else ""
        )
    pyproject = PYTHON_ROOT / "openai-agents-sdk" / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'"zep-cloud([^"]*)"', text)
        found[str(pyproject.relative_to(REPO_ROOT))] = (
            f"zep-cloud{match.group(1)}" if match else ""
        )
    return found


def _is_v328_pin(req: str) -> bool:
    if not req.startswith("zep-cloud"):
        return False
    spec = req[len("zep-cloud") :].strip()
    if spec == "==3.28.0":
        return True
    return bool(re.fullmatch(r">=3\.28(\.0)?\s*,\s*<4(\.0(\.0)?)?", spec))


def test_requirements_pin_zep_cloud_v328():
    pins = _zep_requirement_pins()
    assert pins
    bad = {p: v for p, v in pins.items() if not _is_v328_pin(v)}
    assert not bad, (
        "zep-cloud pins must be ==3.28.0 or >=3.28,<4; got:\n"
        + "\n".join(f"  {p}: {v or '(missing)'}" for p, v in sorted(bad.items()))
    )


@pytest.mark.parametrize("nb_path", NOTEBOOKS, ids=lambda p: p.parent.name)
def test_notebooks_do_not_use_removed_memory_apis(nb_path: Path):
    source = _notebook_code(nb_path)
    hits = [pat.pattern for pat in STALE_NOTEBOOK_PATTERNS if pat.search(source)]
    assert not hits, f"{nb_path} still uses removed APIs matching: {hits}"


def test_quickstart_message_role_mapping():
    source = _notebook_code(PYTHON_ROOT / "quickstart" / "quickstart.ipynb")
    assert "role_type=" not in source
    assert re.search(r"Message\s*\(", source)
    assert re.search(r"\brole\s*=", source)


def test_chat_history_memory_has_no_fact_rating_imports():
    path = PYTHON_ROOT / "chat_history" / "memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "zep_cloud" in node.module:
            for alias in node.names:
                imported.add(alias.name)
    assert "FactRatingInstruction" not in imported
    assert "FactRatingExamples" not in imported
    import zep_cloud.types as types

    assert not hasattr(types, "FactRatingInstruction")
    assert not hasattr(types, "FactRatingExamples")


def test_user_example_exception_paths_do_not_reference_undefined_i():
    path = PYTHON_ROOT / "user_example.py"
    source = path.read_text(encoding="utf-8")
    assert "Created session {i+1}" not in source
    assert "Failed to create session {i+1}" not in source
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main":
            for child in ast.walk(node):
                if isinstance(child, ast.JoinedStr):
                    text = ast.unparse(child)
                    if "session" in text.lower() and "{i" in text:
                        pytest.fail(
                            f"session exception f-string still references i: {text}"
                        )


@pytest.mark.parametrize("ui_path", STREAMLIT_UIS, ids=lambda p: p.parent.name)
def test_streamlit_ui_survives_missing_logo(ui_path: Path):
    source = ui_path.read_text(encoding="utf-8")
    assert "def get_zep_logo_base64" in source, f"{ui_path} missing get_zep_logo_base64 helper"
    # Extract function body lines after the def until next top-level def/assignment/decorator
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("def get_zep_logo_base64"))
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        body.append(line)
    func_src = "\n".join(body)

    class _St:
        @staticmethod
        def cache_data(fn=None, **_kwargs):
            if fn is None:
                return lambda f: f
            return fn

    ns: dict = {"st": _St(), "base64": __import__("base64"), "os": os, "Path": Path}
    exec(func_src, ns)
    fn = ns["get_zep_logo_base64"]

    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path.cwd()
        try:
            os.chdir(tmp)
            result = fn()
        finally:
            os.chdir(cwd)
    assert result is None or result == "" or isinstance(result, str)


def test_chunking_cli_matches_readme_contract():
    script = PYTHON_ROOT / "chunking-example" / "chunk_and_ingest.py"
    readme = (PYTHON_ROOT / "chunking-example" / "README.md").read_text(encoding="utf-8")
    source = script.read_text(encoding="utf-8")
    assert "argparse" in source, "chunk_and_ingest.py must implement argparse CLI"
    for flag in ("--user-id", "--chunk-size", "--chunk-overlap", "--dry-run", "--wait"):
        assert flag in source, f"missing {flag} in CLI"
        assert flag in readme, f"missing {flag} in README"

    help_proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--user-id" in help_proc.stdout

    bad = subprocess.run(
        [sys.executable, str(script), "sample_document.txt"],
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode != 0


def test_agent_memory_readme_user_id_matches_populate_script():
    readme = (PYTHON_ROOT / "agent-memory-full-example" / "README.md").read_text(
        encoding="utf-8"
    )
    populate = (
        PYTHON_ROOT
        / "agent-memory-full-example"
        / "pre-populate-memories"
        / "populate-memories.py"
    ).read_text(encoding="utf-8")
    match = re.search(r'USER_ID\s*=\s*["\']([^"\']+)["\']', populate)
    assert match, "populate-memories.py must define USER_ID"
    user_id = match.group(1)
    assert user_id in readme, f"README must document user ID {user_id!r}"


def test_context_templates_readme_includes_set_context_templates_step():
    readme = (PYTHON_ROOT / "context-templates-example" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "set-context-templates.py" in readme


def test_openai_agents_sdk_readme_clone_path():
    readme = (PYTHON_ROOT / "openai-agents-sdk" / "README.md").read_text(encoding="utf-8")
    assert "zep-python" not in readme
    assert "examples/python/openai-agents-sdk" in readme


def test_python_sources_compile():
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(PYTHON_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("nb_path", NOTEBOOKS, ids=lambda p: p.parent.name)
def test_notebook_code_cells_parse(nb_path: Path):
    source = _notebook_code(nb_path)
    cleaned_lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("%", "!")):
            continue
        if stripped.startswith("pip install"):
            continue
        cleaned_lines.append(line)
    try:
        ast.parse("\n".join(cleaned_lines))
    except SyntaxError as exc:
        pytest.fail(f"{nb_path} code cells have SyntaxError: {exc}")


def test_elevenlabs_proxy_syntax_and_no_stale_memory_api():
    proxy = PYTHON_ROOT / "elevenlabs-zep-example" / "llm-proxy" / "proxy_server.py"
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(proxy)],
        check=True,
        capture_output=True,
        text=True,
    )
    source = proxy.read_text(encoding="utf-8")
    assert "zep.memory" not in source
    assert "memory.add_session" not in source


def test_no_invalid_get_user_context_mode_kwarg_in_py_examples():
    offenders: list[str] = []
    for path in PYTHON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"get_user_context\([^\)]*mode\s*=", text, re.S):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"get_user_context(..., mode=) still present in: {offenders}"


def test_no_get_user_context_mode_in_readmes():
    offenders: list[str] = []
    for path in PYTHON_ROOT.rglob("README.md"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"get_user_context\([^\)]*mode\s*=", text, re.S):
            offenders.append(str(path.relative_to(REPO_ROOT)))
        if 'mode="basic"' in text or "mode='basic'" in text:
            # Only flag when adjacent to get_user_context docs
            if "get_user_context" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"README still documents mode= for get_user_context: {offenders}"


def test_chat_history_docstring_matches_get_user_context():
    path = PYTHON_ROOT / "chat_history" / "memory.py"
    source = path.read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source)) or ""
    assert "get_user_context" in doc or "user context" in doc.lower()
    assert "MMR" not in doc
    assert "Searching the thread memory" not in doc


def _load_chunking_module():
    script = PYTHON_ROOT / "chunking-example" / "chunk_and_ingest.py"
    spec = importlib.util.spec_from_file_location("chunk_and_ingest", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Avoid executing OpenAI/Zep imports at module level beyond stdlib
    spec.loader.exec_module(module)
    return module


def test_chunking_wait_helper_exists_and_is_not_sleep_only():
    script = PYTHON_ROOT / "chunking-example" / "chunk_and_ingest.py"
    source = script.read_text(encoding="utf-8")
    assert re.search(r"def wait_for_episode\(", source), (
        "chunk_and_ingest.py must define wait_for_episode()"
    )
    tree = ast.parse(source)
    fn = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "wait_for_episode"
        ),
        None,
    )
    assert fn is not None
    body_src = ast.unparse(fn)
    assert "episode.get" in body_src or "graph.episode.get" in source
    assert "processed" in body_src
    assert "TimeoutError" in body_src or "timeout" in body_src.lower()
    # Must not be only a bare sleep
    assert not re.fullmatch(
        r".*sleep\(\s*\d+(\.\d+)?\s*\).*", body_src.replace("\n", " ")
    )
    # process_document must call wait_for_episode when wait=True
    assert "wait_for_episode(" in source


def test_wait_for_episode_success_timeout_and_error():
    module = _load_chunking_module()
    wait_for_episode = module.wait_for_episode

    class Episode:
        def __init__(self, processed=False, task_id=None):
            self.processed = processed
            self.task_id = task_id

    class FakeEpisodeAPI:
        def __init__(self, sequence):
            self.sequence = list(sequence)
            self.calls = 0

        def get(self, uuid_: str):
            self.calls += 1
            if not self.sequence:
                return Episode(False)
            item = self.sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class FakeTaskAPI:
        def __init__(self, status="failed", error="boom"):
            self.status = status
            self.error = error
            self.calls = 0

        def get(self, task_id: str):
            self.calls += 1

            class Task:
                pass

            task = Task()
            task.status = self.status
            task.error = self.error
            return task

    class FakeGraph:
        def __init__(self, episode_api):
            self.episode = episode_api

    class FakeClient:
        def __init__(self, episode_api, task_api=None):
            self.graph = FakeGraph(episode_api)
            self.task = task_api

    # Success after polling (episode.processed is primary completion signal)
    episode_api = FakeEpisodeAPI([Episode(False), Episode(False), Episode(True)])
    client = FakeClient(episode_api)
    result = wait_for_episode(
        client, "ep-1", timeout_seconds=5, poll_interval_seconds=0, sleep_fn=lambda _s: None
    )
    assert result.processed is True
    assert episode_api.calls == 3

    # Timeout when never processed
    episode_api = FakeEpisodeAPI([Episode(False)] * 20)
    client = FakeClient(episode_api)
    with pytest.raises(TimeoutError):
        wait_for_episode(
            client,
            "ep-2",
            timeout_seconds=0.01,
            poll_interval_seconds=0,
            sleep_fn=lambda _s: None,
        )

    # Fail-fast on canonical unsuccessful task statuses
    for bad_status in ("failed", "error", "canceled", "cancelled", "partial"):
        episode_api = FakeEpisodeAPI([Episode(False, task_id="task-1")])
        task_api = FakeTaskAPI(status=bad_status, error=f"{bad_status} boom")
        client = FakeClient(episode_api, task_api=task_api)
        with pytest.raises(RuntimeError, match="(?i)fail|error|cancel|partial"):
            wait_for_episode(
                client,
                f"ep-{bad_status}",
                timeout_seconds=5,
                poll_interval_seconds=0,
                sleep_fn=lambda _s: None,
            )


def _extract_status_set_literals(source: str) -> list[set[str]]:
    """Return set literals assigned/compared in wait helpers (best-effort via AST)."""
    tree = ast.parse(source)
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Set) and all(isinstance(e, ast.Constant) for e in node.elts):
            values = {e.value for e in node.elts if isinstance(e.value, str)}
            if values:
                found.append(values)
        if isinstance(node, (ast.List, ast.Tuple)) and all(
            isinstance(e, ast.Constant) for e in node.elts
        ):
            values = {e.value for e in node.elts if isinstance(e.value, str)}
            # Only keep status-like collections
            if values & {
                "failed",
                "error",
                "canceled",
                "cancelled",
                "partial",
                "succeeded",
                "completed",
                "complete",
            }:
                found.append(values)
    return found


def test_chunking_and_notebook_wait_helpers_use_canonical_task_statuses():
    required_failure = {"failed", "error", "canceled", "cancelled", "partial"}
    required_success = {"succeeded", "completed", "complete"}

    chunk_src = (PYTHON_ROOT / "chunking-example" / "chunk_and_ingest.py").read_text(
        encoding="utf-8"
    )
    # Chunking fail-fast set must include canceled + partial (episode.processed remains primary success)
    assert "canceled" in chunk_src and "cancelled" in chunk_src
    assert "partial" in chunk_src
    chunk_sets = _extract_status_set_literals(chunk_src)
    assert any(required_failure <= s for s in chunk_sets), (
        f"chunking wait helper missing failure statuses {required_failure}; found {chunk_sets}"
    )

    for nb_name in ("langgraph-agent", "autogen-agent"):
        source = _notebook_code(PYTHON_ROOT / nb_name / "agent.ipynb")
        assert "canceled" in source and "cancelled" in source
        assert "partial" in source
        sets = _extract_status_set_literals(source)
        assert any(required_failure <= s for s in sets), (
            f"{nb_name} missing failure statuses {required_failure}; found {sets}"
        )
        assert any(required_success <= s for s in sets), (
            f"{nb_name} missing success statuses {required_success}; found {sets}"
        )


def test_langgraph_notebook_has_tool_loop_search_and_bounded_wait():
    source = _notebook_code(PYTHON_ROOT / "langgraph-agent" / "agent.ipynb")
    assert "graph.search" in source
    assert "@tool" in source or "StructuredTool" in source or "tool(" in source
    assert "ToolNode" in source or "tools" in source
    assert "add_conditional_edges" in source or "should_continue" in source
    assert re.search(r"wait_for_|TimeoutError|poll_interval|timeout_seconds", source)
    # Non-interactive grounded-recall demo questions
    assert "dog" in source.lower() or "work" in source.lower()
    assert "ask(" in source or "graph_invoke(" in source or "ainvoke(" in source


def test_autogen_notebook_has_memory_search_wait_and_compatible_pin():
    nb_path = PYTHON_ROOT / "autogen-agent" / "agent.ipynb"
    source = _notebook_code(nb_path)
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    all_text = "\n".join("".join(c.get("source", [])) for c in nb["cells"])

    assert "get_user_context" in source
    assert "graph.search" in source
    assert re.search(r"wait_for_|TimeoutError|poll_interval|timeout_seconds", source)
    assert "ConversableAgent" in source or "AssistantAgent" in source

    # Pinned compatible Autogen install (classic ConversableAgent API)
    pin_match = re.search(
        r"pyautogen\s*([><=!~][^`\s]+)|autogen==([^\s`]+)|pyautogen==([^\s`]+)",
        all_text,
    )
    assert pin_match, (
        "autogen notebook must pin a compatible Autogen package "
        "(e.g. pyautogen>=0.2.35,<0.3)"
    )


#: Packages that only publish wheels for newer interpreters than the examples
#: floor (Python 3.10). They must carry a python_version marker so installing
#: on a supported interpreter does not fail the whole requirements file.
NEWER_PYTHON_ONLY_PACKAGES = {"audioop-lts"}


def _requirement_files() -> list[Path]:
    return [
        PYTHON_ROOT / "requirements.txt",
        *sorted(PYTHON_ROOT.glob("*/requirements.txt")),
        *sorted(PYTHON_ROOT.glob("*/*/requirements.txt")),
        *sorted(PYTHON_ROOT.glob("requirements-dev.txt")),
    ]


@pytest.mark.parametrize(
    "req_path", _requirement_files(), ids=lambda p: str(p.relative_to(PYTHON_ROOT))
)
def test_requirements_guard_newer_python_only_packages(req_path: Path):
    """Pins needing a newer Python than our floor must be marker-guarded.

    Without a marker, `pip install -r requirements.txt` aborts on Python 3.10-3.12
    and every downstream check fails.
    """
    offenders = []
    for line in req_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = re.split(r"[<>=!~;\[ ]", stripped, maxsplit=1)[0].strip().lower()
        if name in NEWER_PYTHON_ONLY_PACKAGES and "python_version" not in stripped:
            offenders.append(stripped)
    assert not offenders, (
        f"{req_path.relative_to(REPO_ROOT)} needs a python_version marker for: {offenders}"
    )


def test_requirements_dev_provides_pytest():
    dev = PYTHON_ROOT / "requirements-dev.txt"
    assert dev.exists(), "examples/python/requirements-dev.txt must exist"
    text = dev.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "-r requirements.txt" in text


def test_simple_py_does_not_wipe_project_ontology():
    source = (PYTHON_ROOT / "simple.py").read_text(encoding="utf-8")
    assert not re.search(r"set_entity_types\(\s*entities\s*=\s*\{\s*\}", source)


def test_user_example_only_mutates_created_users():
    source = (PYTHON_ROOT / "user_example.py").read_text(encoding="utf-8")
    assert "created_user_ids" in source
    assert "list_ordered" not in source


def test_chat_history_wait_is_not_fixed_sleep():
    source = (PYTHON_ROOT / "chat_history" / "memory.py").read_text(encoding="utf-8")
    assert "asyncio.sleep(50)" not in source
    assert "wait_for_task" in source
    assert "TASK_FAILURE_STATUSES" in source
