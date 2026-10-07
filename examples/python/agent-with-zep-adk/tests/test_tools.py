"""Unit tests for handles, budgets, tool gating, and result markers."""

from __future__ import annotations

from types import SimpleNamespace

from agent_with_zep_adk.config import MAX_TOOL_CALLS
from agent_with_zep_adk.handles import HandleRegistry
from agent_with_zep_adk.tools import build_tools


def _tool(name, deps):
    return {tool.__name__: tool for tool in build_tools(deps)}[name]


# 1. HandleRegistry


def test_same_uuid_same_handle_and_seen():
    reg = HandleRegistry()
    h1 = reg.register("uuid-a", "n")
    assert reg.register("uuid-a", "n") == h1
    assert h1 == "n1"
    assert not reg.is_seen(h1)
    reg.mark_seen(h1)
    assert reg.is_seen(h1)


def test_prefixes():
    reg = HandleRegistry()
    assert reg.register("u1", "n") == "n1"
    assert reg.register("u2", "e") == "e1"
    assert reg.register("u3", "p") == "p1"
    assert reg.register("u4", "n") == "n2"


def test_reset_seen():
    reg = HandleRegistry()
    handle = reg.register("uuid-a", "n")
    reg.mark_seen(handle)

    reg.reset_seen()

    assert not reg.is_seen(handle)


# 2. Budget and dedup


async def test_budget_exhaustion(deps):
    fn = _tool("list_nodes", deps)
    deps.calls_left = 0
    out = await fn(label="Product")
    assert "budget exhausted" in out
    assert not any(c[0] == "get_by_graph_id" for c in deps.zep.graph.calls)


async def test_thirteenth_call_is_budget_notice(deps):
    fn = _tool("list_nodes", deps)
    for i in range(MAX_TOOL_CALLS):
        out = await fn(label="Product", limit=i + 1)
        assert "budget exhausted" not in out
    out = await fn(label="Product", limit=99)
    assert "budget exhausted" in out


async def test_duplicate_call_notice(deps):
    fn = _tool("list_nodes", deps)
    await fn(label="Product")
    calls_before = len(deps.zep.graph.calls)
    out = await fn(label="Product")
    assert "duplicate call" in out
    assert len(deps.zep.graph.calls) == calls_before


# 4. search_context argument mapping


async def test_search_context_filters(deps, fake_zep):
    fn = _tool("search_context", deps)
    # register a node so near= handles resolve
    some_uuid = next(iter(fake_zep.graph.nodes))
    handle = deps.registry.register(some_uuid, "n")
    out = await fn(
        query="flow under-delivery",
        scope="edges",
        report_type="complaint_summary",
        product="Aster 410",
        near=[handle],
        limit=50,
    )
    name, kwargs = fake_zep.graph.calls[-1]
    assert name == "search"
    assert kwargs["reranker"] == "cross_encoder"
    assert kwargs["limit"] == 20  # capped
    assert kwargs["bfs_origin_node_uuids"] == [some_uuid]
    group = kwargs["search_filters"].episode_metadata_filters
    assert group.type == "and"
    props = {(f.property_name, f.property_value, f.comparison_operator) for f in group.filters}
    assert props == {
        ("report_type", "complaint_summary", "="),
        ("product", "Aster 410", "="),
    }
    assert out.startswith("ranked sample")


# 5. list_nodes / get_neighborhood markers


async def test_list_nodes_complete(deps):
    out = await _tool("list_nodes", deps)(label="Product", limit=50)
    assert out.startswith("complete")
    assert "Aster 410" in out


async def test_list_nodes_truncated(deps):
    out = await _tool("list_nodes", deps)(label="Product", limit=2)
    assert out.startswith("truncated at 2")


async def test_get_neighborhood(deps):
    out = await _tool("list_nodes", deps)(label="Product")
    handle = deps.registry._by_handle["n" + "1"].handle  # first registered node
    # find the Aster 410 handle from the listing instead
    handle = deps.registry._by_uuid[
        next(
            u
            for u, e in deps.registry._by_uuid.items()
            if deps.zep.graph.nodes[u].name == "Aster 410"
        )
    ].handle
    out = await _tool("get_neighborhood", deps)(handle=handle, edge_types=["CONTAINS"])
    assert out.startswith("complete")
    assert "FS-20" in out


async def test_get_details_episode(deps, fake_zep):
    ep = next(iter(fake_zep.graph.episodes.values()))
    handle = deps.registry.register(ep.uuid_, "p")
    out = await _tool("get_details", deps)(handle=handle)
    assert "R01" in out and "metadata" in out


# 6. get_employees


async def test_get_employees_team(deps):
    out = await _tool("get_employees", deps)(team="Reliability Engineering")
    for name in ["Sanjay Mehta", "Lena Vogt", "Owen Briggs", "Aiko Tanaka"]:
        assert name in out
    assert "Priya Raman" not in out


async def test_get_employees_product(deps):
    out = await _tool("get_employees", deps)(product="Aster 410")
    assert "Lena Vogt" in out and "Owen Briggs" in out


async def test_get_employees_intersection(deps):
    out = await _tool("get_employees", deps)(team="Reliability Engineering", product="Aster 410")
    assert "Lena Vogt" in out
    assert "Owen Briggs" in out
    assert "Aiko Tanaka" not in out
    assert "Sanjay Mehta (" not in out


# 7. search_products


async def test_search_products_filings(deps):
    out = await _tool("search_products", deps)()
    assert "Aster 410" in out
    assert "region=EU" in out and "status=valid" in out
    assert out.startswith("complete")


async def test_search_products_query_filter(deps):
    out = await _tool("search_products", deps)(query="Lyric")
    assert "Lyric 300" in out and "Lyric 350" in out
    assert "Aster 410" not in out


# 8. Page-size clamping (the list API returns at most 50 nodes per page)


def _add_nodes(fake_zep, label, prefix, count):
    for i in range(count):
        n = SimpleNamespace(
            uuid_=f"{prefix}-{i}",
            name=f"{prefix} {i}",
            labels=["Entity", label],
            attributes={},
            summary="",
            degree=0,
        )
        fake_zep.graph.nodes[n.uuid_] = n


async def test_list_nodes_truncated_at_api_page_size(deps, fake_zep):
    _add_nodes(fake_zep, "Team", "Extra Team", 50)
    out = await _tool("list_nodes", deps)(label="Team")
    assert out.startswith("truncated at 49")
    assert sum(1 for line in out.splitlines() if line.startswith("- ")) == 49


async def test_search_products_truncated_at_api_page_size(deps, fake_zep):
    _add_nodes(fake_zep, "Product", "Extra Product", 50)
    out = await _tool("search_products", deps)()
    assert out.startswith("truncated at 49")


async def test_get_employees_no_match_in_truncated_list(deps, fake_zep):
    _add_nodes(fake_zep, "Team", "Extra Team", 50)
    out = await _tool("get_employees", deps)(team="No Such Team")
    assert "in the first 50 Team nodes" in out
    assert "search_context" in out


async def test_get_employees_no_match_complete_list(deps):
    out = await _tool("get_employees", deps)(team="No Such Team")
    assert out == "no Team node named 'No Such Team'."


async def test_ingest_reset_removes_orientation_cache(fake_zep, tmp_path, monkeypatch):
    from agent_with_zep_adk import ingest as ingest_mod
    from agent_with_zep_adk import orientation

    monkeypatch.setattr(orientation, "CACHE_DIR", tmp_path)
    cache = orientation.cache_path("test-graph")
    cache.write_text("{}")
    await ingest_mod.ingest(fake_zep, "test-graph", reset=True)
    assert not cache.exists()


def test_summarize_excludes_run_errors():
    from eval.run_eval import summarize

    records = [
        {
            "config": "A",
            "run_error": "boom",
            "grade_error": None,
            "latency_s": 0.0,
            "tool_call_count": 0,
            "tool_selection": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "context_completeness": None,
            "grade_accuracy": None,
            "plan_quality": None,
        },
        {
            "config": "A",
            "latency_s": 5.0,
            "tool_call_count": 3,
            "tool_selection": 1,
            "input_tokens": 100,
            "output_tokens": 10,
            "context_completeness": 0.5,
            "grade_accuracy": 2,
            "plan_quality": 1,
        },
    ]
    row = summarize(records)[0]
    assert row["latency_s"] == 5.0
    assert row["run_errors"] == 1
    assert row["grade_errors"] == 0
