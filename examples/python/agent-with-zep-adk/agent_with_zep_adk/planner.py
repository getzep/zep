"""Retrieval planning and tool gating for the Google ADK agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .config import MAX_PLANS

if TYPE_CHECKING:
    from .tools import AgentDeps

RETRIEVAL_TOOL_NAMES = {
    "search_context",
    "list_nodes",
    "get_neighborhood",
    "get_details",
    "get_employees",
    "search_products",
}


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


def make_submit_plan(deps: AgentDeps) -> Callable:
    """Create a plan submission tool bound to one agent run."""

    def submit_plan(plan: RetrievalPlan) -> str:
        """Submit your retrieval plan before you call a retrieval tool. You can submit one revised plan later if the evidence is incomplete or contradicts the plan."""
        if isinstance(plan, dict):
            plan = RetrievalPlan.model_validate(plan)
        deps.plans.append(plan)
        remaining = MAX_PLANS - len(deps.plans)
        if remaining > 0:
            return f"Plan accepted. Run the plan steps now with the retrieval tools. You may submit {remaining} revised plan(s) later."
        return "Plan accepted. Run the plan steps now with the retrieval tools. You cannot submit more plans."

    return submit_plan


def offered_tool_names(planning: bool, n_plans: int) -> set[str]:
    """Return the tool names that are valid for the current planning state."""
    if not planning:
        return set(RETRIEVAL_TOOL_NAMES)
    if n_plans == 0:
        return {"submit_plan"}
    if n_plans >= MAX_PLANS:
        return set(RETRIEVAL_TOOL_NAMES)
    return {*RETRIEVAL_TOOL_NAMES, "submit_plan"}


def make_before_model_callback(deps: AgentDeps, planning: bool) -> Callable:
    """Filter model tool declarations to the tools offered in this run."""

    def before_model_callback(callback_context, llm_request) -> None:
        offered = offered_tool_names(planning, len(deps.plans))
        tools = []
        for tool in llm_request.config.tools or []:
            declarations = getattr(tool, "function_declarations", None)
            if declarations is None:
                tools.append(tool)
                continue
            filtered = [declaration for declaration in declarations if declaration.name in offered]
            if filtered:
                tools.append(tool.model_copy(update={"function_declarations": filtered}))
        llm_request.config.tools = tools

    return before_model_callback


def make_before_tool_callback(deps: AgentDeps, planning: bool) -> Callable:
    """Reject a tool call that is not offered without changing the call budget."""

    def before_tool_callback(tool, args, tool_context) -> dict | None:
        if tool.name in offered_tool_names(planning, len(deps.plans)):
            return None
        if tool.name in RETRIEVAL_TOOL_NAMES and planning and not deps.plans:
            return {
                "result": "Call submit_plan with your retrieval plan before you call a retrieval tool."
            }
        if tool.name == "submit_plan" and (not planning or len(deps.plans) >= MAX_PLANS):
            return {"result": "You cannot submit more plans. Run the retrieval tools and answer."}
        return {"result": f"The tool {tool.name} is not offered for this run."}

    return before_tool_callback
