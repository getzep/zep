import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

import claude_agent_sdk_agent
from banking import make_case
from claude_agent_sdk_agent import TraceHooks, build_tools, record_assistant_text
from tools import BankingTools


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None, str | None]] = []

    def append(
        self,
        event_type: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self.events.append((event_type, content, tool_name, tool_call_id))


def test_claude_hooks_and_mcp_tool_expose_tool_error() -> None:
    trace = RecordingTrace()
    hooks = TraceHooks(trace)  # type: ignore[arg-type]
    mcp_tools = build_tools(BankingTools(make_case(10134), []))
    read_document = next(item for item in mcp_tools if item.name == "read_document")

    result = asyncio.run(read_document.handler({"document_id": "missing"}))
    assert result == {
        "content": [{"type": "text", "text": "The document ID is not in list_documents."}],
        "is_error": True,
    }
    asyncio.run(
        hooks.pre_tool_use(
            {
                "tool_name": "mcp__banking__read_document",
                "tool_input": {"document_id": "missing"},
                "tool_use_id": "call-1",
            }
        )
    )
    asyncio.run(
        hooks.post_tool_use(
            {
                "tool_name": "mcp__banking__read_document",
                "tool_response": result,
                "tool_use_id": "call-1",
            }
        )
    )
    asyncio.run(
        hooks.post_tool_use_failure(
            {
                "tool_name": "mcp__banking__read_document",
                "error": "The document ID is not in list_documents.",
                "tool_use_id": "call-1",
            }
        )
    )
    record_assistant_text(trace, "I could not read that document.")  # type: ignore[arg-type]

    assert [event[0] for event in trace.events] == [
        "tool_call",
        "tool_result",
        "decision",
    ]
    assert trace.events[0] == (
        "tool_call",
        '{"document_id": "missing"}',
        "read_document",
        "call-1",
    )
    assert trace.events[1][1] == "The document ID is not in list_documents."
    assert trace.events[1][2:] == ("read_document", "call-1")


def test_compare_packages_uses_typed_schema_and_validates_arguments() -> None:
    tools = build_tools(BankingTools(make_case(10134), []))
    compare = next(item for item in tools if item.name == "compare_packages")
    packages_schema = compare.input_schema["properties"]["packages"]
    assert packages_schema["type"] == "array"
    selection_name = packages_schema["items"]["$ref"].rsplit("/", maxsplit=1)[-1]
    assert compare.input_schema["$defs"][selection_name]["type"] == "object"

    case = make_case(10134)
    args = {"packages": [{"account_id": account.account_id} for account in case.savings]}
    result = asyncio.run(compare.handler(args))
    assert "is_error" not in result
    assert json.loads(result["content"][0]["text"])["permitted"] is True

    invalid = asyncio.run(compare.handler({}))
    assert invalid["is_error"] is True


@pytest.mark.parametrize(
    ("sdk_result", "expected_answer"),
    [("Final SDK answer.", "Final SDK answer."), ("", "Last assistant answer.")],
)
def test_result_message_controls_final_answer_and_token_count(
    sdk_result: str, expected_answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_query(*, prompt: str, options: object) -> AsyncIterator[Any]:
        del prompt, options
        yield AssistantMessage(
            content=[TextBlock(text="Earlier answer.")], model="claude-haiku-5-5"
        )
        yield AssistantMessage(
            content=[TextBlock(text="Last assistant answer.")], model="claude-haiku-5-5"
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="test-session",
            result=sdk_result,
            usage={
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            },
        )

    monkeypatch.setattr(claude_agent_sdk_agent, "query", fake_query)
    trace = RecordingTrace()
    result = asyncio.run(
        claude_agent_sdk_agent.run_episode(
            make_case(10134),
            "claude-haiku-5-5",
            "none",
            [],
            trace,  # type: ignore[arg-type]
            False,
        )
    )
    assert result.final_answer == expected_answer
    assert result.tokens == 10
