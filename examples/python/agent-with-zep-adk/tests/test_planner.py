"""Tool gating tests: planning on/off controls which tools are offered."""

from __future__ import annotations

from types import SimpleNamespace

from google.adk.models.llm_request import LlmRequest
from google.adk.tools.function_tool import FunctionTool

from agent_with_zep_adk.planner import (
    RETRIEVAL_TOOL_NAMES,
    RetrievalPlan,
    make_before_model_callback,
    make_before_tool_callback,
    make_submit_plan,
    offered_tool_names,
)
from agent_with_zep_adk.tools import MAX_TOOL_CALLS, build_tools


def _plan():
    return RetrievalPlan(
        subjects=["Aster 410"],
        steps=[{"tool": "search_context", "purpose": "find reports", "arguments_hint": "query"}],
        evidence_needed=["status"],
        stop_when="done",
    )


def test_offered_tool_names():
    assert offered_tool_names(False, 0) == RETRIEVAL_TOOL_NAMES
    assert offered_tool_names(True, 0) == {"submit_plan"}
    assert offered_tool_names(True, 1) == RETRIEVAL_TOOL_NAMES | {"submit_plan"}
    assert offered_tool_names(True, 2) == RETRIEVAL_TOOL_NAMES


def test_before_model_callback_filters_declarations(deps):
    functions = build_tools(deps) + [make_submit_plan(deps)]
    request = LlmRequest()
    request.append_tools([FunctionTool(fn) for fn in functions])
    original_tools_dict = request.tools_dict

    make_before_model_callback(deps, planning=True)(None, request)

    declarations = [
        declaration.name
        for tool in request.config.tools
        for declaration in tool.function_declarations or []
    ]
    assert declarations == ["submit_plan"]
    assert request.tools_dict is original_tools_dict


def test_before_tool_callback_blocks_retrieval_before_plan(deps):
    callback = make_before_tool_callback(deps, planning=True)

    result = callback(SimpleNamespace(name="search_context"), {"query": "products"}, None)

    assert result == {
        "result": "Call submit_plan with your retrieval plan before you call a retrieval tool."
    }
    assert deps.calls_left == MAX_TOOL_CALLS
    assert not deps.call_log


def test_before_tool_callback_blocks_plan_after_limit(deps):
    from agent_with_zep_adk.config import MAX_PLANS

    deps.plans.extend(_plan() for _ in range(MAX_PLANS))
    callback = make_before_tool_callback(deps, planning=True)

    result = callback(SimpleNamespace(name="submit_plan"), {}, None)

    assert result == {"result": "You cannot submit more plans. Run the retrieval tools and answer."}
    assert deps.calls_left == MAX_TOOL_CALLS


def test_submit_plan_records_and_caps_plans(deps):
    submit_plan = make_submit_plan(deps)
    assert "Plan accepted" in submit_plan(_plan())
    assert "cannot submit more plans" in submit_plan(_plan())
    assert len(deps.plans) == 2


def test_build_tools_returns_retrieval_tools_bound_to_dependencies(deps):
    names = [tool.__name__ for tool in build_tools(deps)]
    assert names == [
        "search_context",
        "list_nodes",
        "get_neighborhood",
        "get_details",
        "get_employees",
        "search_products",
    ]
