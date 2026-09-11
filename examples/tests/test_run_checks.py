"""Tests for examples/run_checks.py inventory and credential gating.

These tests intentionally import the runner module before implementation exists
(TDD). Run from repo root:

  python3 -m pytest examples/tests/test_run_checks.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXAMPLES_ROOT.parent
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

import run_checks  # noqa: E402


REQUIRED_GROUP_IDS = {
    # Python
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
    # TypeScript
    "typescript-root",
    "typescript-chunking-example",
    "typescript-langgraph",
    "typescript-eve",
    "typescript-zep-graph-visualization",
    # Go
    "go-root",
    "go-chunking-example",
}


def test_inventory_includes_all_repaired_runnable_groups():
    ids = {g.id for g in run_checks.INVENTORY}
    missing = REQUIRED_GROUP_IDS - ids
    assert not missing, f"inventory missing groups: {sorted(missing)}"


def test_inventory_paths_exist():
    for group in run_checks.INVENTORY:
        path = EXAMPLES_ROOT / group.rel_path
        assert path.exists(), f"{group.id} path missing: {path}"


def test_default_cli_mode_is_static():
    args = run_checks.parse_args([])
    assert args.mode == "static"


def test_live_mode_flag_parsed():
    args = run_checks.parse_args(["--mode", "live"])
    assert args.mode == "live"
    args = run_checks.parse_args(["--live"])
    assert args.mode == "live"


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
    # Anthropic-only live smokes must still be skipped
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
    assert "ELEVENLABS" in " ".join(meta.values()).upper() or any(
        "eleven" in k.lower() for k in meta
    )
    eleven = next(g for g in run_checks.INVENTORY if g.id == "python-elevenlabs-zep-example")
    assert list(eleven.live) == []
    assert eleven.not_live_covered


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


def test_static_default_does_not_mutate_hint():
    """Guardrail: documented default must be static / non-mutating."""
    assert run_checks.DEFAULT_MODE == "static"
    assert run_checks.default_mutates_zep() is False


@pytest.mark.parametrize("key", ["ZEP_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"])
def test_primary_keys_documented_on_matching_groups(key: str):
    groups_mentioning = [
        g.id
        for g in run_checks.INVENTORY
        if key in g.required_keys_docs or any(key in c.required_keys for c in g.live)
    ]
    assert groups_mentioning, f"no inventory group documents {key}"
