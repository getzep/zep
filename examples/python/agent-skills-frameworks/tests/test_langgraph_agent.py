import asyncio
from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from banking import make_case
from langgraph_agent import build_model, run_episode


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    tool_errors: list[str] = Field(default_factory=list)
    parallel_tool_calls: list[bool | None] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        del tools, tool_choice, kwargs
        self.parallel_tool_calls.append(parallel_tool_calls)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.tool_errors.extend(
            str(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
            and message.status == "error"
            and "not in list_documents" in str(message.content)
        )
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, run_manager, **kwargs)


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


@pytest.mark.parametrize(
    ("effort", "expected_effort", "expected_thinking"),
    [
        ("none", None, {"type": "disabled"}),
        ("low", "low", {"type": "adaptive"}),
        ("max", "max", {"type": "adaptive"}),
    ],
)
def test_anthropic_effort_mapping(
    effort: str,
    expected_effort: str | None,
    expected_thinking: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = build_model("anthropic:claude-haiku-5-5", effort)
    assert isinstance(model, ChatAnthropic)
    assert model.effort == expected_effort
    assert model.thinking == expected_thinking
    assert model.max_retries == 0


def test_tool_callbacks_record_order_and_expose_tool_error() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_document",
                        "args": {"document_id": "missing"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="I could not read that document."),
        ]
    )
    recorder = RecordingTrace()
    result = asyncio.run(
        run_episode(
            make_case(10134),
            "anthropic:fake",
            "none",
            [],
            recorder,  # type: ignore[arg-type]
            False,
            test_model=model,
        )
    )
    assert result.final_answer == "I could not read that document."
    assert model.tool_errors == ["The document ID is not in list_documents."]
    assert [event[0] for event in recorder.events] == [
        "input_reference",
        "tool_call",
        "tool_result",
        "decision",
    ]
    assert recorder.events[1] == (
        "tool_call",
        '{"document_id": "missing"}',
        "read_document",
        "call-1",
    )
    assert recorder.events[2][1] == '"The document ID is not in list_documents."'
    assert recorder.events[2][2:] == ("read_document", "call-1")


def test_parallel_tool_calls_are_rejected_before_execution() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_case",
                        "args": {},
                        "id": f"call-{index}",
                        "type": "tool_call",
                    }
                    for index in range(2)
                ],
            )
        ]
    )
    with pytest.raises(RuntimeError, match="parallel tool calls"):
        asyncio.run(
            run_episode(
                make_case(10134),
                "anthropic:fake",
                "none",
                [],
                RecordingTrace(),  # type: ignore[arg-type]
                False,
                test_model=model,
            )
        )
    assert len(model.responses) == 0
