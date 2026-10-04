"""Tests for the retrieval guard, the grade validator, and the Report type."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelRetry

from agent_with_zep import ontology
from agent_with_zep.agent import require_retrieval
from agent_with_zep.config import AS_OF_DATE
from agent_with_zep.prompts import ROLE, render_system_prompt
from eval.run_eval import Grade, validate_grade

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
