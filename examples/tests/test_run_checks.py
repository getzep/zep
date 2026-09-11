"""Tests for examples/run_checks.py inventory, installs, and credential gating.

Run from repo root:

  python3 -m pytest examples/tests/test_run_checks.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXAMPLES_ROOT.parent
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

import run_checks  # noqa: E402


REQUIRED_GROUP_IDS = {
    "python-root",
    "python-graph_example",
    "python-chat_history",
    "python-chunking-example",
    "python-claude-prompt-caching-example",
    "python-openai-agents-sdk",
    "python-agent-memory-full-example",
    "python-context-templates-example",
    "python-user-summary-instructions-example",
    "python-zep-quickstart-dashboard",
    "python-elevenlabs-zep-example",
    "typescript-root",
    "typescript-chunking-example",
    "typescript-langgraph",
    "typescript-eve",
    "typescript-zep-graph-visualization",
    "go-root",
    "go-chunking-example",
}

FIXED_DESTRUCTIVE_IDS = (
    "claude-caching-demo-dana",
    "eve-demo-user",
    "eve-demo-company",
)


def test_inventory_includes_all_repaired_runnable_groups():
    ids = {g.id for g in run_checks.INVENTORY}
    missing = REQUIRED_GROUP_IDS - ids
    assert not missing, f"inventory missing groups: {sorted(missing)}"


def test_compileall_checks_exclude_virtualenvs_and_vendored_code():
    """compileall must not descend into .venv/site-packages/node_modules.

    Those trees contain third-party sources written for newer Python syntax and
    are not part of the examples.
    """
    compile_checks = [
        check
        for group in run_checks.INVENTORY
        for check in group.static
        if "compileall" in check.argv
    ]
    assert compile_checks, "expected compileall static checks"
    for check in compile_checks:
        assert "-x" in check.argv, f"{check.cwd}/{check.name} must pass compileall -x"
        pattern = check.argv[check.argv.index("-x") + 1]
        for vendored in (".venv/lib/python3.13/site-packages/anyio/_core/_tasks.py",
                         "venv/lib/python3.12/site-packages/click/utils.py",
                         "node_modules/foo/bar.py"):
            assert re.search(pattern, vendored), (
                f"exclude pattern {pattern!r} must skip {vendored}"
            )
        assert not re.search(pattern, "chunking-example/chunk_and_ingest.py")
        assert not re.search(pattern, "graph_example/entity_types.py")


def test_python_commands_use_placeholder_not_launcher_interpreter():
    """Python argv must be resolvable to a selected interpreter at run time."""
    python_checks = [
        check
        for group in run_checks.INVENTORY
        for check in (*group.static, *group.live)
        if check.argv and check.argv[0] == run_checks.PYTHON_PLACEHOLDER
    ]
    assert python_checks, "expected python checks to use the placeholder"
    for group in run_checks.INVENTORY:
        for check in (*group.static, *group.live):
            assert check.argv[0] != sys.executable, (
                f"{group.id}/{check.name} hardcodes the launching interpreter"
            )


def test_pip_installs_run_through_selected_interpreter():
    pip_steps = [
        step
        for group in run_checks.INVENTORY
        for step in group.install
        if "pip" in step.argv
    ]
    assert pip_steps, "expected pip install steps"
    for step in pip_steps:
        assert step.argv[0] == run_checks.PYTHON_PLACEHOLDER, (
            f"{step.name} must install via '<python> -m pip', got {step.argv}"
        )
        assert step.argv[1:3] == ["-m", "pip"]


def test_resolve_python_argv_substitutes_placeholder():
    check = run_checks._check(
        "demo", [run_checks.PYTHON_PLACEHOLDER, "-m", "compileall"], "python"
    )
    resolved = run_checks.resolve_python_argv(check.argv, "/opt/py/bin/python3")
    assert resolved[0] == "/opt/py/bin/python3"
    assert resolved[1:] == ["-m", "compileall"]


def test_python_version_preflight_rejects_old_interpreters():
    ok, message = run_checks.check_python_version((3, 9, 6), min_version=(3, 10))
    assert ok is False
    assert "3.10" in message
    assert "3.9.6" in message
    # actionable remediation, not just a failure
    assert "venv" in message.lower()

    ok, message = run_checks.check_python_version((3, 12, 3), min_version=(3, 10))
    assert ok is True
    assert message == ""


def test_pytest_is_installed_by_the_group_that_runs_it():
    """Any group whose static checks run pytest must install pytest."""
    for group in run_checks.INVENTORY:
        runs_pytest = any(
            "pytest" in check.argv for check in group.static
        )
        if not runs_pytest:
            continue
        install_blob = " ".join(
            " ".join(step.argv) + " " + step.manifest for step in group.install
        )
        manifests = [EXAMPLES_ROOT / step.manifest for step in group.install]
        declared = any(
            "pytest" in path.read_text(encoding="utf-8")
            for path in manifests
            if path.exists()
        )
        assert declared or "pytest" in install_blob, (
            f"{group.id} runs pytest but no install step provides it"
        )


def test_install_manifests_exist_on_disk():
    for group in run_checks.INVENTORY:
        for step in group.install:
            assert (EXAMPLES_ROOT / step.manifest).exists(), (
                f"{group.id}/{step.name} manifest missing: {step.manifest}"
            )


def test_formatting_shows_real_interpreter_not_placeholder():
    check = run_checks._check(
        "demo", [run_checks.PYTHON_PLACEHOLDER, "-m", "pytest"], "python"
    )
    rendered = run_checks._format_check(check, "/opt/py/bin/python3")
    assert "/opt/py/bin/python3" in rendered
    assert run_checks.PYTHON_PLACEHOLDER not in rendered

    step = run_checks._install(
        "demo", [run_checks.PYTHON_PLACEHOLDER, "-m", "pip", "install", "-r", "r.txt"],
        "python", "python/requirements.txt",
    )
    rendered_step = run_checks._format_install(step, "/opt/py/bin/python3")
    assert "/opt/py/bin/python3" in rendered_step
    assert run_checks.PYTHON_PLACEHOLDER not in rendered_step


def test_failure_summary_lists_failed_checks():
    failures = [
        ("install", "python", "pip-requirements"),
        ("static", "python", "v3-regression"),
    ]
    summary = run_checks.format_failure_summary(failures)
    assert "pip-requirements" in summary
    assert "v3-regression" in summary
    assert "python" in summary
    assert summary.count("\n") >= 2


def test_python_flag_selects_interpreter():
    args = run_checks.parse_args([])
    assert args.python == sys.executable

    args = run_checks.parse_args(["--python", "/opt/py/bin/python3"])
    assert args.python == "/opt/py/bin/python3"


def test_inventory_paths_exist():
    for group in run_checks.INVENTORY:
        path = EXAMPLES_ROOT / group.rel_path
        assert path.exists(), f"{group.id} path missing: {path}"


def test_default_cli_mode_is_static_without_install():
    args = run_checks.parse_args([])
    assert args.mode == "static"
    assert args.install is False


def test_live_mode_flag_parsed():
    args = run_checks.parse_args(["--mode", "live"])
    assert args.mode == "live"
    args = run_checks.parse_args(["--live"])
    assert args.mode == "live"


def test_install_flag_parsed_and_default_skips_install():
    assert run_checks.parse_args([]).install is False
    assert run_checks.parse_args(["--install"]).install is True
    plan = run_checks.build_plan(mode="static", env={}, include_install=False)
    assert plan.install_steps == []
    assert plan.static_checks, "default assumes existing dependencies; static checks still run"


def test_static_plan_never_includes_live_checks():
    plan = run_checks.build_plan(mode="static", env={})
    assert plan.static_checks, "static mode must schedule static checks"
    assert plan.live_checks == []


def test_static_plan_excludes_interactive_servers_and_notebooks():
    plan = run_checks.build_plan(mode="static", env={})
    joined = " ".join(" ".join(c.argv) for c in plan.static_checks).lower()
    for banned in (
        "streamlit",
        "jupyter",
        "papermill",
        "eve dev",
        "next dev",
        "npm start",
        "yarn start",
        "npm run dev",
        "yarn dev",
    ):
        assert banned not in joined, f"static plan must not run {banned!r}"


def test_live_checks_declare_required_keys():
    for group in run_checks.INVENTORY:
        for check in group.live:
            assert check.required_keys, f"{group.id}/{check.name} missing required_keys"


def test_credential_gating_skips_all_live_when_keys_absent():
    plan = run_checks.build_plan(mode="live", env={})
    assert plan.live_checks == []
    assert plan.skipped_live, "expected skips explaining missing credentials"


def test_credential_gating_runs_zep_only_when_only_zep_present():
    plan = run_checks.build_plan(mode="live", env={"ZEP_API_KEY": "z"})
    assert plan.live_checks, "ZEP_API_KEY should unlock at least one live smoke"
    for check in plan.live_checks:
        assert set(check.required_keys) <= {"ZEP_API_KEY"}
        assert "OPENAI_API_KEY" not in check.required_keys
        assert "ANTHROPIC_API_KEY" not in check.required_keys
        assert "GOOGLE_API_KEY" not in check.required_keys


def test_credential_gating_requires_all_listed_keys():
    env = {"ZEP_API_KEY": "z", "OPENAI_API_KEY": "o"}
    plan = run_checks.build_plan(mode="live", env=env)
    for check in plan.live_checks:
        assert set(check.required_keys) <= set(env)
    skipped_names = {s.name for s in plan.skipped_live}
    anthropic_live = [
        c.name
        for g in run_checks.INVENTORY
        for c in g.live
        if "ANTHROPIC_API_KEY" in c.required_keys
    ]
    for name in anthropic_live:
        assert name in skipped_names


def test_tavily_and_elevenlabs_are_marked_not_live_covered():
    meta = run_checks.NOT_LIVE_COVERED
    assert "TAVILY_API_KEY" in meta
    blob = " ".join(meta.keys()) + " " + " ".join(meta.values())
    assert "PROXY_API_KEY" in blob
    assert "VITE_ELEVENLABS_AGENT_ID" in blob
    eleven = next(g for g in run_checks.INVENTORY if g.id == "python-elevenlabs-zep-example")
    assert list(eleven.live) == []
    assert eleven.not_live_covered
    assert "PROXY_API_KEY" in eleven.required_keys_docs
    assert "VITE_ELEVENLABS_AGENT_ID" in eleven.required_keys_docs


def test_elevenlabs_llm_proxy_requirements_manifest_exists_and_pins_zep():
    req = EXAMPLES_ROOT / "python/elevenlabs-zep-example/llm-proxy/requirements.txt"
    assert req.is_file(), "llm-proxy must ship a real requirements.txt"
    text = req.read_text(encoding="utf-8")
    for pkg in ("fastapi", "uvicorn", "openai", "zep-cloud", "python-dotenv"):
        assert pkg in text, f"{pkg} missing from {req}"
    assert ">=3.28" in text and "<4" in text


def test_install_steps_are_structural_and_map_to_existing_manifests():
    assert hasattr(run_checks, "InstallStep")
    for group in run_checks.INVENTORY:
        assert group.install, f"{group.id} missing install steps"
        for step in group.install:
            assert isinstance(step, run_checks.InstallStep), (
                f"{group.id} install entries must be InstallStep, got {type(step)}"
            )
            assert step.argv and all(isinstance(a, str) for a in step.argv)
            joined = " ".join(step.argv)
            assert "#" not in joined, (
                f"{group.id}/{step.name} argv must not embed prose alternatives: {step.argv}"
            )
            assert " or " not in joined.lower()
            manifest = EXAMPLES_ROOT / step.manifest
            assert manifest.is_file(), (
                f"{group.id}/{step.name} manifest missing: {manifest}"
            )
            cwd = EXAMPLES_ROOT / step.cwd
            assert cwd.is_dir(), f"{group.id}/{step.name} cwd missing: {cwd}"


def test_install_plan_schedules_before_static_and_dedupes():
    plan = run_checks.build_plan(mode="static", env={}, include_install=True)
    assert plan.install_steps, "include_install must schedule install steps"
    assert plan.static_checks
    keys = [(tuple(s.argv), s.cwd) for s in plan.install_steps]
    assert len(keys) == len(set(keys)), "install steps must be deduplicated"


def test_openai_agents_install_keeps_pip_alternative_in_notes_only():
    group = next(g for g in run_checks.INVENTORY if g.id == "python-openai-agents-sdk")
    for step in group.install:
        joined = " ".join(step.argv)
        assert "pip install -e" not in joined
        assert "#" not in joined
    assert "pip install -e" in (group.install_notes or "")


def test_live_run_prefix_is_unique_and_stable_shape():
    a = run_checks.make_run_prefix()
    b = run_checks.make_run_prefix()
    assert a != b
    assert a.startswith("exrun-")
    assert b.startswith("exrun-")


def test_live_check_commands_receive_unique_user_thread_prefixes():
    env = {
        "ZEP_API_KEY": "z",
        "OPENAI_API_KEY": "o",
        "ANTHROPIC_API_KEY": "a",
        "GOOGLE_API_KEY": "g",
    }
    plan = run_checks.build_plan(mode="live", env=env, run_prefix="exrun-testhost-1")
    assert plan.live_checks
    for check in plan.live_checks:
        blob = " ".join(check.argv) + " " + " ".join(
            f"{k}={v}" for k, v in (check.env or {}).items()
        )
        assert "exrun-testhost-1" in blob, (
            f"{check.name} must embed unique run prefix in argv/env, got: {blob!r}"
        )


def test_fixed_id_live_checks_consume_prefix_env_and_avoid_bare_destructive_ids():
    plan = run_checks.build_plan(
        mode="live",
        env={"ZEP_API_KEY": "z"},
        run_prefix="exrun-testhost-1",
    )
    claude = next(c for c in plan.live_checks if "ingest" in c.name)
    assert claude.env.get("ZEP_EXAMPLE_USER_PREFIX", "").startswith("exrun-testhost-1")
    claude_blob = " ".join(claude.argv) + " " + " ".join(claude.env.values())
    for token in FIXED_DESTRUCTIVE_IDS:
        assert token not in claude_blob

    eve = next(c for c in plan.live_checks if c.name == "smoke")
    assert eve.env.get("ZEP_DEMO_USER_ID", "").startswith("exrun-testhost-1")
    assert eve.env.get("ZEP_COMPANY_GRAPH_ID", "").startswith("exrun-testhost-1")
    for token in FIXED_DESTRUCTIVE_IDS:
        assert token not in " ".join(eve.env.values())


def test_claude_ingest_resolves_demo_user_from_env_prefix(monkeypatch):
    claude_dir = str(EXAMPLES_ROOT / "python/claude-prompt-caching-example")
    if claude_dir not in sys.path:
        sys.path.insert(0, claude_dir)
    import ingest as claude_ingest  # noqa: WPS433

    monkeypatch.delenv("ZEP_EXAMPLE_USER_PREFIX", raising=False)
    assert claude_ingest.resolve_demo_user_id() == "claude-caching-demo-dana"
    monkeypatch.setenv("ZEP_EXAMPLE_USER_PREFIX", "exrun-abc-user")
    assert claude_ingest.resolve_demo_user_id() == "exrun-abc-user"


def test_no_live_check_hardcodes_destructive_fixed_ids():
    for group in run_checks.INVENTORY:
        for check in group.live:
            blob = " ".join(check.argv) + " " + " ".join(
                f"{k}={v}" for k, v in check.env.items()
            )
            for token in FIXED_DESTRUCTIVE_IDS:
                assert token not in blob, (
                    f"{group.id}/{check.name} still hardcodes destructive id {token!r}"
                )


def test_uuid_groups_document_uuid_uniqueness_not_env_consumption():
    for group_id in ("python-root", "typescript-root", "go-root"):
        group = next(g for g in run_checks.INVENTORY if g.id == group_id)
        notes = (group.notes or "").lower()
        assert "uuid" in notes, f"{group_id} notes should mention UUID uniqueness"
        assert "zep_example_user_prefix" not in notes


def test_static_default_does_not_mutate_hint():
    """Guardrail: documented default must be static / non-mutating."""
    assert run_checks.DEFAULT_MODE == "static"
    assert run_checks.default_mutates_zep() is False
    assert "existing" in run_checks.DEFAULT_DEPENDENCY_ASSUMPTION.lower()


@pytest.mark.parametrize("key", ["ZEP_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"])
def test_primary_keys_documented_on_matching_groups(key: str):
    groups_mentioning = [
        g.id
        for g in run_checks.INVENTORY
        if key in g.required_keys_docs or any(key in c.required_keys for c in g.live)
    ]
    assert groups_mentioning, f"no inventory group documents {key}"


def test_no_stale_tdd_placeholder_comment():
    text = Path(__file__).read_text(encoding="utf-8")
    docstring = text.split('"""', 2)[1]
    assert "before implementation exists" not in docstring
    assert "(TDD)" not in docstring
