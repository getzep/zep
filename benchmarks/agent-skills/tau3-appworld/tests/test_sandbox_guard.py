from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox_guard import check_sandbox, isolated_command, probe_commands, sandbox_lifecycle


@pytest.mark.parametrize("failure", [None, "search", "export", "initialize"])
def test_sandbox_lifecycle_cleans_repeated_searches(monkeypatch, tmp_path, failure):
    from tau2.domains.banking_knowledge.retrieval import RETRIEVAL_VARIANTS, _create_sandbox
    from tau2.knowledge.sandbox_manager import SandboxManager

    monkeypatch.setattr("tau2.knowledge.sandbox_manager._check_sandbox_dependencies", lambda: None)
    monkeypatch.setattr("tau2.knowledge.sandbox_manager.tempfile.gettempdir", lambda: str(tmp_path))
    document = SimpleNamespace(id="test", title="Test", content="KB document")
    kb = SimpleNamespace(get_all_documents=lambda: [document])

    def fail(*args, **kwargs):
        raise RuntimeError("test failure")

    if failure == "initialize":
        monkeypatch.setattr(SandboxManager, "_create_srt_settings", fail)
    if failure == "export":
        monkeypatch.setattr(SandboxManager, "export_documents", fail)
    for _ in range(2):
        try:
            with sandbox_lifecycle():
                sandbox = _create_sandbox(kb, RETRIEVAL_VARIANTS["alltools"].shell)
                assert sandbox.kb_dir.is_dir()
                if failure == "search":
                    fail()
        except RuntimeError:
            if failure is None:
                raise
        else:
            assert failure is None
        assert list(tmp_path.glob("agentic_search_*")) == []


def test_sandbox_lifecycle_empty_and_nested_scopes(monkeypatch, tmp_path):
    from tau2.knowledge.sandbox_manager import SandboxManager

    monkeypatch.setattr("tau2.knowledge.sandbox_manager._check_sandbox_dependencies", lambda: None)
    with sandbox_lifecycle():
        outer = SandboxManager(base_temp_dir=str(tmp_path))
        with sandbox_lifecycle():
            inner = SandboxManager(base_temp_dir=str(tmp_path))
        assert not inner.sandbox_dir.exists()
        assert outer.sandbox_dir.exists()
        with sandbox_lifecycle():
            pass
    assert list(tmp_path.iterdir()) == []


def test_writable_sandbox_is_forbidden():
    with pytest.raises(ValueError, match="Writable"):
        isolated_command(SimpleNamespace(allow_writes=True), "cat document.txt")


def test_self_check_fails_if_any_probe_succeeds():
    sandbox = SimpleNamespace(run_command=lambda *a, **kw: (0, "document", ""))
    with pytest.raises(RuntimeError, match="isolation failed"):
        check_sandbox(sandbox)


def test_self_check_fails_if_documents_are_unreadable():
    sandbox = SimpleNamespace(run_command=lambda *a, **kw: (1, "", "denied"))
    with pytest.raises(RuntimeError, match="cannot read KB"):
        check_sandbox(sandbox)


def test_self_check_fails_for_empty_document_output():
    sandbox = SimpleNamespace(run_command=lambda *a, **kw: (0, "", ""))
    with pytest.raises(RuntimeError, match="cannot read KB"):
        check_sandbox(sandbox)


def test_self_check_checks_every_forbidden_surface():
    calls = []

    def command(cmd, **kwargs):
        calls.append(cmd)
        return (0, "KB document", "") if len(calls) == 1 else (1, "", "denied")

    results = check_sandbox(SimpleNamespace(run_command=command))
    assert len(results) == len(probe_commands()) + 1
    assert {r["check"] for r in results} >= {
        "tasks_absolute",
        "tasks_traversal",
        "evaluator",
        "results",
        "rerun",
        "memory",
        "environment",
        "network",
        "kb_read",
        "write",
    }


def test_kernel_boundary_uses_only_kb_and_system_mounts(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("sandbox_guard.shutil.which", lambda name: "/usr/bin/" + name)

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="doc", stderr="")

    monkeypatch.setattr("sandbox_guard.subprocess.run", run)
    isolated_command(SimpleNamespace(allow_writes=False, kb_dir=tmp_path), "cat /kb/doc.txt")
    args = calls[0]
    assert "--unshare-all" in args and "--clearenv" in args
    assert "--ro-bind" in args and str(tmp_path) in args
    assert not any(arg in args for arg in (str(Path.home()), "/home", "/"))
    assert args[-1] == "cat /kb/doc.txt"


def test_shell_output_with_invalid_utf8_after_cut_is_replaced(monkeypatch, tmp_path):
    command = (
        'cd /kb; grep -n -o -i "[0-9.]*\\s*%[^|]\\{0,60\\}" '
        'doc_credit_cards_*.md | grep -i "back\\|reward\\|earn\\|point" '
        "| cut -c1-220 | sort | uniq | head -n 120"
    )

    def run(args, **kwargs):
        assert args[-1] == command
        assert kwargs["text"] is False
        return SimpleNamespace(
            returncode=0,
            stdout=b"reward 1% \xc3 invalid",
            stderr=b"warning \xc3",
        )

    monkeypatch.setattr("sandbox_guard.shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr("sandbox_guard.subprocess.run", run)

    code, stdout, stderr = isolated_command(
        SimpleNamespace(allow_writes=False, kb_dir=tmp_path), command
    )

    assert code == 0
    assert stdout == "reward 1% � invalid"
    assert stderr == "warning �"
