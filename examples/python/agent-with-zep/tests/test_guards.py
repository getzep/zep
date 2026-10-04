"""Tests for the retrieval guard, the grade validator, and the Report type."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelRetry

from agent_with_zep import ontology
from agent_with_zep.agent import require_retrieval
from agent_with_zep.config import AS_OF_DATE
from agent_with_zep.prompts import ROLE, render_system_prompt
from agent_with_zep.tools import build_toolset
from eval.run_eval import Grade, validate_grade


def _tool(name):
    return build_toolset().tools[name].function


def _ctx(deps):
    return SimpleNamespace(deps=deps)


# require_retrieval


def test_require_retrieval_raises_with_no_calls(deps):
    with pytest.raises(ModelRetry, match="have not called a retrieval tool"):
        require_retrieval(deps)


def test_require_retrieval_ok_with_one_call(deps):
    deps.log_call("list_nodes", {}, "complete", 1)
    require_retrieval(deps)


def test_require_retrieval_ok_when_budget_spent(deps):
    deps.calls_left = 0
    require_retrieval(deps)


# validate_grade


def _grade(**kw):
    base = {
        "evidence_found": [True, False, True],
        "answer_states": [True, False, True],
        "must_not_violated": False,
        "accuracy": 2,
        "plan_quality": 2,
        "rationale": "The answer states most facts.",
    }
    base.update(kw)
    return Grade(**base)


def test_validate_grade_wrong_evidence_length():
    with pytest.raises(ModelRetry, match="evidence_found must have exactly 3"):
        validate_grade(_grade(evidence_found=[True]), 3)


def test_validate_grade_wrong_answer_states_length():
    with pytest.raises(ModelRetry, match="answer_states must have exactly 3"):
        validate_grade(_grade(answer_states=[]), 3)


def test_validate_grade_placeholder_rationale():
    with pytest.raises(ModelRetry, match="rationale"):
        validate_grade(_grade(rationale="placeholder"), 3)
    with pytest.raises(ModelRetry, match="rationale"):
        validate_grade(_grade(rationale="  Placeholder  "), 3)


def test_validate_grade_empty_rationale():
    with pytest.raises(ModelRetry, match="rationale"):
        validate_grade(_grade(rationale="   "), 3)


def test_validate_grade_passes():
    validate_grade(_grade(), 3)


# Report entity type


def test_report_entity_type():
    assert "Report" in ontology.ENTITY_TYPES
    assert len(ontology.ENTITY_TYPES) == 10


def test_report_in_system_prompt():
    prompt = render_system_prompt(
        role=ROLE,
        as_of_date=AS_OF_DATE,
        domain_knowledge="dk",
        orientation=True,
        planning=True,
        include_domain_knowledge=True,
    )
    assert "Report" in prompt


# Zep API errors go back to the model as tool results


async def test_tool_returns_zep_error_text(deps, monkeypatch):
    from zep_cloud.core.api_error import ApiError

    async def boom(**kw):
        raise ApiError(status_code=502, body="bad gateway")

    monkeypatch.setattr(deps.zep.graph, "search", boom)
    out = await _tool("search_context")(_ctx(deps), query="flow")
    assert out.startswith("zep error 502")
    assert "bad gateway" in out
    assert deps.calls_left == 11  # the call still counted against the budget


async def test_search_context_empty_query_no_budget(deps):
    out = await _tool("search_context")(_ctx(deps), query="   ")
    assert out == "invalid arguments: query must not be empty."
    assert deps.calls_left == 12


async def test_search_context_near_episodes_no_budget(deps, fake_zep):
    some_uuid = next(iter(fake_zep.graph.nodes))
    handle = deps.registry.register(some_uuid, "n")
    out = await _tool("search_context")(_ctx(deps), query="flow", scope="episodes", near=[handle])
    assert (
        out
        == 'near works only with scope="edges" or scope="nodes". Remove near or change the scope.'
    )
    assert deps.calls_left == 12
