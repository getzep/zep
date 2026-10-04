"""Retrieval planning: the plan schema, submit_plan, and tool gating.

When planning is on, the retrieval tools stay hidden until the model submits a
RetrievalPlan. The model may submit at most MAX_PLANS plans (one plan plus one
revision). submit_plan does not count against the retrieval budget.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from .config import MAX_PLANS


class PlanStep(BaseModel):
    tool: Literal[
        "search_context",
        "list_nodes",
        "get_neighborhood",
        "get_details",
        "get_employees",
        "search_products",
    ]
    purpose: str = Field(description="What this step must find and why.")
    arguments_hint: str = Field(
        description="The main arguments, such as the query, label, or handle."
    )


class RetrievalPlan(BaseModel):
    subjects: list[str] = Field(description="The entities the answer depends on.")
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    evidence_needed: list[str] = Field(description="The facts a complete answer must contain.")
    stop_when: str = Field(description="The condition that ends retrieval.")


SUBMIT_PLAN_DOCSTRING = """\
Submit your retrieval plan before you call a retrieval tool. You can submit one revised plan later if the evidence is incomplete or contradicts the plan."""


def add_submit_plan(toolset: FunctionToolset) -> None:
    """Register the submit_plan tool on a toolset."""

    @toolset.tool
    def submit_plan(ctx: RunContext, plan: RetrievalPlan) -> str:
        """Submit your retrieval plan before you call a retrieval tool. You can submit one revised plan later if the evidence is incomplete or contradicts the plan."""
        ctx.deps.plans.append(plan)
        remaining = MAX_PLANS - len(ctx.deps.plans)
        if remaining > 0:
            return f"Plan accepted. Retrieval tools are now available. You may submit {remaining} revised plan(s) later."
        return "Plan accepted. Retrieval tools are now available. You cannot submit more plans."


def make_prepare(planning: bool):
    """Return a ToolsPrepareFunc that gates retrieval tools on the plan.

    With planning on: submit_plan alone is offered until the first plan exists,
    then the retrieval tools appear. submit_plan disappears after MAX_PLANS.
    With planning off: retrieval tools are offered at once and submit_plan is
    absent.
    """
    retrieval_names = {
        "search_context",
        "list_nodes",
        "get_neighborhood",
        "get_details",
        "get_employees",
        "search_products",
    }

    def prepare(ctx: RunContext, tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
        if not planning:
            return [t for t in tool_defs if t.name != "submit_plan"]
        if len(ctx.deps.plans) == 0:
            return [t for t in tool_defs if t.name == "submit_plan"]
        if len(ctx.deps.plans) >= MAX_PLANS:
            return [t for t in tool_defs if t.name in retrieval_names]
        return tool_defs

    return prepare
