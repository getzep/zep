from typing import Literal

from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage

from anthropic_client import CONTENT_ITEM, _messages, _response
from episode_audit import reasoning_parts


def _message(stop_reason: Literal["tool_use", "max_tokens"] = "tool_use") -> Message:
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=[
            TextBlock(type="text", text="I will check the balance."),
            ToolUseBlock(type="tool_use", id="toolu_1", name="get_balance", input={"id": "A"}),
        ],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(
            input_tokens=10,
            output_tokens=7,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=90,
        ),
    )


def test_response_maps_tool_calls_text_and_cache_usage() -> None:
    response = _response(_message())

    assert response.output_text == "I will check the balance."
    assert [item["type"] for item in response.output] == [CONTENT_ITEM, "message", "function_call"]
    call = response.output[2]
    assert (call["call_id"], call["name"], call["arguments"]) == (
        "toolu_1",
        "get_balance",
        '{"id": "A"}',
    )
    assert response.status == "completed"
    assert response.usage.input_tokens == 1000
    assert response.usage.input_tokens_details.cached_tokens == 900
    assert response.usage.input_tokens_details.cache_creation_tokens == 90
    assert response.usage.output_tokens == 7


def test_response_marks_max_tokens_as_incomplete() -> None:
    assert _response(_message("max_tokens")).status == "incomplete"


def test_native_thinking_survives_the_adapter_for_episode_audits():
    message = _message()
    message.content.insert(
        0, ThinkingBlock(type="thinking", thinking="Check the balance.", signature="signature")
    )
    response = _response(message)
    assert reasoning_parts(response.output) == [{"kind": "thinking", "text": "Check the balance."}]
    assert response.output_text == "I will check the balance."


def test_messages_replay_native_content_and_tool_results() -> None:
    response = _response(_message())
    items = [
        {"role": "user", "content": "What is my balance?"},
        *response.output,
        {"type": "function_call_output", "call_id": "toolu_1", "output": '{"balance": 5}'},
        {"role": "user", "content": [{"type": "input_text", "text": "Thanks."}]},
    ]

    messages = _messages(items)

    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == response.output[0]["content"]
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"balance": 5}'},
        {"type": "text", "text": "Thanks."},
    ]


def test_messages_keep_plain_assistant_messages() -> None:
    messages = _messages([{"role": "assistant", "content": "Hi"}])

    assert messages == [{"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}]
