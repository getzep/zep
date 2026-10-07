"""Offline end-to-end tests for ADK tool calls and retrieval retries."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

from agent_with_zep_adk.agent import build_agent, run_agent
from agent_with_zep_adk.config import AgentConfig


class ScriptedLlm(BaseLlm):
    responses: list[LlmResponse]
    requests: list[LlmRequest] = Field(default_factory=list)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.requests.append(llm_request)
        yield self.responses.pop(0)


def _call(name: str, call_id: str, args: dict) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))],
        ),
        turn_complete=True,
    )


def _answer(text: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Internal thought.", thought=True),
                types.Part(text=text),
            ],
        ),
        turn_complete=True,
    )


def _plan_args() -> dict:
    return {
        "plan": {
            "subjects": ["Pemberline products"],
            "steps": [
                {
                    "tool": "list_nodes",
                    "purpose": "List the products.",
                    "arguments_hint": "label=Product",
                }
            ],
            "evidence_needed": ["Product names"],
            "stop_when": "The product list is complete.",
        }
    }


def _agent(deps, config: AgentConfig, model: ScriptedLlm) -> LlmAgent:
    return build_agent(config, deps, model=model)


async def test_config_d_runs_plan_retrieval_and_answer(deps):
    model = ScriptedLlm(
        model="scripted",
        responses=[
            _call("submit_plan", "plan-1", _plan_args()),
            _call("list_nodes", "nodes-1", {"label": "Product"}),
            _answer("The graph lists the products."),
        ],
    )

    result = await run_agent(_agent(deps, AgentConfig(), model), deps, "List the products.")

    assert len(result.plans) == 1
    assert [call["name"] for call in result.tool_calls] == ["list_nodes"]
    assert result.answer == "The graph lists the products."


async def test_run_retries_when_model_answers_before_retrieval(deps):
    model = ScriptedLlm(
        model="scripted",
        responses=[
            _answer("I will answer without evidence."),
            _call("search_context", "search-1", {"query": "EU product clearance"}),
            _answer("The graph has no matching evidence."),
        ],
    )

    result = await run_agent(
        _agent(deps, AgentConfig(planning=False), model),
        deps,
        "Which products have EU clearance?",
    )

    assert [call["name"] for call in result.tool_calls] == ["search_context"]
    assert result.answer == "The graph has no matching evidence."
    assert any(
        "You have not called a retrieval tool" in (part.text or "")
        for content in model.requests[1].contents
        for part in content.parts or []
    )


async def test_run_agent_registers_graph_sample_handles(deps):
    orientation = {
        "nodes": [{"uuid": "sample-uuid", "name": "Aster 410", "labels": ["Entity", "Product"]}]
    }
    model = ScriptedLlm(
        model="scripted",
        responses=[
            _call("search_context", "search-1", {"query": "EU product clearance"}),
            _answer("The graph has no matching evidence."),
        ],
    )

    question = "Which products have EU clearance?"
    await run_agent(
        _agent(deps, AgentConfig(planning=False), model),
        deps,
        question,
        orientation=orientation,
    )

    assert deps.registry.resolve("n1") == "sample-uuid"
    assert "graph_data" in model.requests[0].contents[0].parts[0].text
    assert model.requests[0].contents[0].parts[1].text == question
