import asyncio
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

from adk_agent import build_config, run_episode
from banking import make_case


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


class ScriptedLlm(BaseLlm):
    responses: list[LlmResponse]
    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
        del stream
        self.requests.append(llm_request)
        yield self.responses.pop(0)


def test_adk_effort_mapping() -> None:
    no_effort = build_config("none")
    maximum_effort = build_config("max")
    assert no_effort.effort is None
    assert no_effort.thinking_config is not None
    assert no_effort.thinking_config.thinking_budget == 0
    assert maximum_effort.effort == "max"
    assert maximum_effort.thinking_config is not None
    assert maximum_effort.thinking_config.thinking_budget == -1


def test_adk_callbacks_record_order_and_expose_tool_error() -> None:
    model = ScriptedLlm(
        model="scripted",
        responses=[
            LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="call-1",
                                name="read_document",
                                args={"document_id": "missing"},
                            )
                        )
                    ],
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=7),
            ),
            LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="I could not read that document.")],
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=3),
            ),
        ],
    )
    recorder = RecordingTrace()
    result = asyncio.run(
        run_episode(
            make_case(10134),
            "claude-haiku-5-5",
            "none",
            [],
            recorder,  # type: ignore[arg-type]
            False,
            test_model=model,
        )
    )

    assert result.final_answer == "I could not read that document."
    assert result.tokens == 10
    assert "The document ID is not in list_documents." in str(model.requests[-1].contents)
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
    assert "The document ID is not in list_documents." in recorder.events[2][1]
    assert recorder.events[2][2] == "read_document"
