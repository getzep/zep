"""End-to-end run for config D with a scripted FunctionModel and fake Zep."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_with_zep.agent import build_agent, run_agent
from agent_with_zep.config import AgentConfig


def _scripted_model():
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "submit_plan",
                        {
                            "plan": {
                                "subjects": ["Aster 410"],
                                "steps": [
                                    {
                                        "tool": "list_nodes",
                                        "purpose": "list products",
                                        "arguments_hint": "label=Product",
                                    }
                                ],
                                "evidence_needed": ["products"],
                                "stop_when": "listed",
                            }
                        },
                    )
                ]
            )
        if calls["n"] == 2:
            return ModelResponse(parts=[ToolCallPart("list_nodes", {"label": "Product"})])
        return ModelResponse(parts=[TextPart("Final answer: Aster 410 and others [n1].")])

    return FunctionModel(fn)


async def test_end_to_end_config_d(deps):
    agent = build_agent(
        AgentConfig(tools="full", orientation=True, domain_knowledge=True, planning=True),
        _scripted_model(),
        deps,
    )
    result = await run_agent(agent, deps, "List the products.", orientation=None)
    assert len(result.plans) == 1
    names = [c["name"] for c in result.tool_calls]
    assert "list_nodes" in names
    assert "Aster 410" in result.answer


def _answering_model():
    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")]))


async def test_run_agent_registers_sample_handles(deps):
    agent = build_agent(
        AgentConfig(planning=False, domain_knowledge=False, orientation=True),
        _answering_model(),
        deps,
    )
    orientation = {"nodes": [{"uuid": "u-1", "name": "Aster 410", "labels": ["Entity", "Product"]}]}
    await run_agent(agent, deps, "Hi", orientation=orientation)
    assert deps.registry.resolve("n1") == "u-1"
