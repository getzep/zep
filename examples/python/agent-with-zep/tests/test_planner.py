"""Tool gating tests: planning on/off controls which tools are offered."""

from __future__ import annotations

from types import SimpleNamespace

from agent_with_zep.planner import RetrievalPlan, make_prepare
from agent_with_zep.tools import build_toolset


async def _offered(deps, planning):
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    toolset = build_toolset()
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    original = await toolset.get_tools(ctx)
    defs = [t.tool_def for t in original.values()]
    offered = make_prepare(planning)(ctx, defs)
    return {t.name for t in offered}


def _plan():
    return RetrievalPlan(
        subjects=["Aster 410"],
        steps=[{"tool": "search_context", "purpose": "find reports", "arguments_hint": "query"}],
        evidence_needed=["status"],
        stop_when="done",
    )


async def test_planning_hides_retrieval_until_plan(deps):
    assert await _offered(deps, True) == {"submit_plan"}
    deps.plans.append(_plan())
    offered = await _offered(deps, True)
    assert "search_context" in offered and "submit_plan" in offered
    deps.plans.append(_plan())
    offered = await _offered(deps, True)
    assert "submit_plan" not in offered
    assert "search_context" in offered


async def test_planning_off(deps):
    offered = await _offered(deps, False)
    assert "submit_plan" not in offered
    assert "search_context" in offered
    assert "search_products" in offered


def test_submit_plan_records_plan(deps):
    fn = build_toolset().tools["submit_plan"].function
    out = fn(SimpleNamespace(deps=deps), plan=_plan())
    assert "Plan accepted" in out and len(deps.plans) == 1
    out = fn(SimpleNamespace(deps=deps), plan=_plan())
    assert "cannot submit more plans" in out
