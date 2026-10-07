"""Offline end-to-end tests for ADK tool calls and retrieval retries."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

import agent_with_zep_adk.agent as agent_module
from agent_with_zep_adk.agent import build_agent, run_agent
from agent_with_zep_adk.config import AgentConfig
from agent_with_zep_adk.planner import RETRIEVAL_TOOL_NAMES
from agent_with_zep_adk.tools import MAX_TOOL_CALLS


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


def _multi(*calls: tuple[str, str, dict]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))
                for name, call_id, args in calls
            ],
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


def _offered_tools(request: LlmRequest) -> set[str]:
    return {
        declaration.name
        for tool in request.config.tools or []
        for declaration in tool.function_declarations or []
    }


def _function_responses(request: LlmRequest) -> list[types.FunctionResponse]:
    return [
        part.function_response
        for content in request.contents
        for part in content.parts or []
        if part.function_response
    ]


async def _run_once(agent: LlmAgent) -> list:
    service = InMemorySessionService()
    runner = Runner(app_name="agent_with_zep_adk", agent=agent, session_service=service)
    session = await service.create_session(app_name="agent_with_zep_adk", user_id="test")
    return [
        event
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Question")]),
        )
    ]


def _answer_text(events: list) -> str:
    return "".join(
        part.text
        for event in events
        if event.is_final_response() and event.content
        for part in event.content.parts or []
        if part.text and not part.thought
    )


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
    assert _offered_tools(model.requests[0]) == {"submit_plan"}
    assert _offered_tools(model.requests[1]) == RETRIEVAL_TOOL_NAMES | {"submit_plan"}


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


@pytest.mark.parametrize(
    ("tool_name", "args", "planning"),
    [
        ("list_nodes", {"label": "Product", "limit": None}, False),
        ("list_nodes", {"label": "Widget"}, False),
        (
            "submit_plan",
            {
                "plan": {
                    "subjects": ["Pemberline products"],
                    "steps": [],
                    "evidence_needed": ["Product names"],
                    "stop_when": "The product list is complete.",
                }
            },
            True,
        ),
    ],
    ids=["null-limit", "invalid-label", "empty-plan-steps"],
)
async def test_invalid_tool_arguments_return_errors_without_budget_use(
    deps, tool_name, args, planning
):
    model = ScriptedLlm(
        model="scripted",
        responses=[_call(tool_name, "invalid-1", args), _answer("done")],
    )

    events = await _run_once(_agent(deps, AgentConfig(planning=planning), model))

    response = next(
        response
        for response in _function_responses(model.requests[1])
        if response.name == tool_name
    )
    assert "argument validation errors" in response.response["error"]
    assert _answer_text(events) == "done"
    assert deps.calls_left == MAX_TOOL_CALLS
    assert not deps.call_log


async def test_string_limit_is_coerced_and_uses_budget(deps):
    model = ScriptedLlm(
        model="scripted",
        responses=[
            _call("list_nodes", "nodes-1", {"label": "Product", "limit": "5"}),
            _answer("done"),
        ],
    )

    events = await _run_once(_agent(deps, AgentConfig(planning=False), model))

    assert _answer_text(events) == "done"
    assert deps.calls_left == MAX_TOOL_CALLS - 1
    assert deps.call_log[0]["args"]["limit"] == 5
    assert deps.zep.graph.calls[-1][1]["limit"] == 6


@pytest.mark.parametrize(
    "calls",
    [
        (
            ("submit_plan", "plan-1", _plan_args()),
            ("list_nodes", "nodes-1", {"label": "Product"}),
        ),
        (
            ("list_nodes", "nodes-1", {"label": "Product"}),
            ("submit_plan", "plan-1", _plan_args()),
        ),
    ],
    ids=["plan-first", "retrieval-first"],
)
async def test_parallel_plan_and_retrieval_uses_request_tool_snapshot(deps, calls):
    model = ScriptedLlm(
        model="scripted",
        responses=[_multi(*calls), _answer("done")],
    )

    events = await _run_once(_agent(deps, AgentConfig(), model))

    list_response = next(
        response
        for response in _function_responses(model.requests[1])
        if response.name == "list_nodes"
    )
    assert list_response.response == {
        "result": "Call submit_plan with your retrieval plan before you call a retrieval tool."
    }
    assert len(deps.plans) == 1
    assert not deps.call_log
    assert deps.calls_left == MAX_TOOL_CALLS
    assert _answer_text(events) == "done"


def _usage(*, candidates: int, thoughts: int) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=1000,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
        total_token_count=1300,
    )


async def _run_usage_case(deps, usage: types.GenerateContentResponseUsageMetadata):
    retrieval = _call("search_context", "search-1", {"query": "products"})
    retrieval.usage_metadata = usage
    answer = _answer("done")
    answer.usage_metadata = usage
    model = ScriptedLlm(model="scripted", responses=[retrieval, answer])
    return await run_agent(
        _agent(deps, AgentConfig(planning=False), model),
        deps,
        "Question",
    )


async def test_litellm_usage_does_not_count_reasoning_twice(deps):
    result = await _run_usage_case(deps, _usage(candidates=300, thoughts=200))

    assert result.output_tokens == 600


async def test_gemini_usage_includes_thought_tokens(deps):
    result = await _run_usage_case(deps, _usage(candidates=100, thoughts=200))

    assert result.output_tokens == 600


async def test_max_tokens_error_stops_without_retry(deps):
    model = ScriptedLlm(
        model="scripted",
        responses=[
            LlmResponse(
                error_code="MAX_TOKENS",
                error_message="max tokens",
                turn_complete=True,
            )
        ],
    )

    with pytest.raises(RuntimeError, match="MAX_TOKENS: max tokens"):
        await run_agent(
            _agent(deps, AgentConfig(planning=False), model),
            deps,
            "Question",
        )

    assert len(model.requests) == 1


async def test_domain_knowledge_braces_reach_model_unchanged(deps, monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "_read_domain_knowledge",
        lambda: "Use {customer_id} to find the customer.",
    )
    model = ScriptedLlm(model="scripted", responses=[_answer("done")])

    events = await _run_once(_agent(deps, AgentConfig(planning=False), model))

    assert "{customer_id}" in model.requests[0].config.system_instruction
    assert _answer_text(events) == "done"
