"""Tests for the review fixes: direction, attributes, handle validation, ingest."""

from __future__ import annotations

from types import SimpleNamespace

from agent_with_zep.ingest import report_episode_kwargs
from agent_with_zep.tools import build_toolset


def _tool(name):
    return build_toolset().tools[name].function


def _ctx(deps):
    return SimpleNamespace(deps=deps)


# 1. get_employees direction: manager vs direct report


async def test_manager_is_not_a_direct_report(deps):
    out = await _tool("get_employees")(_ctx(deps), team="Quality")
    # Priya Raman reports to Helen Okafor and has Marcus Feld as a direct
    # report. With direction="out" only the manager is returned.
    priya_line = next(line for line in out.splitlines() if "Priya Raman" in line)
    assert "manager: Helen Okafor" in priya_line
    assert "manager: Marcus Feld" not in priya_line


async def test_employees_args_checked_before_budget(deps):
    out = await _tool("get_employees")(_ctx(deps))
    assert out == "pass team, product, or both."
    assert deps.calls_left == 12


async def test_employees_case_insensitive_name(deps):
    out = await _tool("get_employees")(_ctx(deps), team="  reliability engineering ")
    assert "Lena Vogt" in out


# 3. node lines show attributes


async def test_node_lines_show_attributes(deps):
    out = await _tool("list_nodes")(_ctx(deps), label="QualityIssue")
    assert "severity=high" in out
    assert "status=open" in out
    assert "issue_id=QI-1041" in out


# 4. handle validation happens before the gate


async def test_search_context_unknown_near_handle(deps):
    out = await _tool("search_context")(_ctx(deps), query="x", near=["n999"])
    assert "unknown handle n999" in out
    assert deps.calls_left == 12
    assert not any(c[0] == "search" for c in deps.zep.graph.calls)


async def test_get_neighborhood_unknown_handle(deps):
    out = await _tool("get_neighborhood")(_ctx(deps), handle="n999")
    assert "unknown handle n999" in out
    assert deps.calls_left == 12


async def test_get_details_unknown_handle(deps):
    out = await _tool("get_details")(_ctx(deps), handle="p999")
    assert "unknown handle p999" in out
    assert deps.calls_left == 12


# 2. get_neighborhood direction


async def test_get_neighborhood_direction(deps, fake_zep):
    # Aster 410 product node: CONTAINS edges go out; REPORTED edges come in on QI.
    aster = next(n for n in fake_zep.graph.nodes.values() if n.name == "Aster 410")
    handle = deps.registry.register(aster.uuid_, "n")
    out = await _tool("get_neighborhood")(
        _ctx(deps), handle=handle, edge_types=["CONTAINS"], direction="out"
    )
    assert "FS-20" in out
    name, kwargs = fake_zep.graph.calls[-1]
    assert name == "get_neighbors"
    assert kwargs["direction"] == "out"


# 5. report episode kwargs


def test_report_episode_kwargs_dates():
    report = {
        "meta": {
            "report_id": "R06",
            "report_type": "sales_note",
            "product": "Aster 410",
            "date": __import__("datetime").date(2026, 5, 2),
            "author": "Erik Sandoval",
        },
        "body": "body text",
    }
    kw = report_episode_kwargs(report)
    assert kw["created_at"] == "2026-05-02T00:00:00Z"
    assert kw["metadata"]["date"] == "2026-05-02"
    assert isinstance(kw["metadata"]["date"], str)
    assert kw["metadata"]["report_id"] == "R06"


def test_settings_reads_zep_base_url(monkeypatch):
    from agent_with_zep.config import Settings

    monkeypatch.setenv("ZEP_API_KEY", "k")
    monkeypatch.setenv("ZEP_BASE_URL", "https://zep.example.com/api/v2")
    s = Settings.from_env(require_zep_key=False)
    assert s.zep_base_url == "https://zep.example.com/api/v2"

    monkeypatch.delenv("ZEP_BASE_URL")
    s = Settings.from_env(require_zep_key=False)
    assert s.zep_base_url is None
