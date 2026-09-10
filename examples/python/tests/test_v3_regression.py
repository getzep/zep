"""
Offline regression harness for examples/python vs zep-cloud v3.28.

Captures known failures without calling live Zep/OpenAI services.

Run from repo root:
  python -m pytest examples/python/tests/test_v3_regression.py -q
"""

from __future__ import annotations

import ast
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
