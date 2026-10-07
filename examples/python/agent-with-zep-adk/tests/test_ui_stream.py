"""Vercel AI UI message stream encoding tests."""

from __future__ import annotations

import json

from google.adk.events import Event
from google.genai import types

from agent_with_zep_adk.ui_stream import encode_ui_stream


async def _events(*events):
    for event in events:
        yield event


def _chunks(lines):
    return [json.loads(line[6:]) for line in lines if line.startswith("data: {")]


async def test_ui_stream_text_tools_and_done():
    events = [
        Event(
            author="zep_analyst",
            partial=True,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Private thought.", thought=True),
                    types.Part(text="Answer"),
                ],
            ),
        ),
        Event(
            author="zep_analyst",
            partial=False,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Private thought.", thought=True),
                    types.Part(text="Answer"),
                ],
            ),
        ),
        Event(
            author="zep_analyst",
            partial=False,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="call-1",
                            name="search_context",
                            args={"query": "EU products"},
                        )
                    )
                ],
            ),
        ),
        Event(
            author="search_context",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="call-1",
                            name="search_context",
                            response={"result": "evidence"},
                        )
                    )
                ],
            ),
        ),
    ]

    lines = [line async for line in encode_ui_stream(_events(*events), message_id="message-1")]
    chunks = _chunks(lines)
    types_seen = [chunk["type"] for chunk in chunks]

    assert types_seen == [
        "start",
        "start-step",
        "text-start",
        "text-delta",
        "text-end",
        "finish-step",
        "start-step",
        "tool-input-start",
        "tool-input-available",
        "finish-step",
        "tool-output-available",
        "finish",
    ]
    assert chunks[-1] == {"type": "finish"}
    assert lines[-1] == "data: [DONE]\n\n"
    assert next(chunk for chunk in chunks if chunk["type"] == "tool-output-available")[
        "output"
    ] == ("evidence")
    assert [chunk["delta"] for chunk in chunks if chunk["type"] == "text-delta"] == ["Answer"]


async def test_ui_stream_deduplicates_calls_and_encodes_errors():
    call = types.Part(
        function_call=types.FunctionCall(
            id="call-1",
            name="search_context",
            args={"query": "EU products"},
        )
    )
    duplicate_call = Event(
        author="zep_analyst",
        content=types.Content(role="model", parts=[call, call]),
    )
    lines = [
        line async for line in encode_ui_stream(_events(duplicate_call), message_id="message-2")
    ]
    chunks = _chunks(lines)
    assert sum(chunk["type"] == "tool-input-available" for chunk in chunks) == 1

    async def broken_events():
        raise RuntimeError("stream failed")
        yield

    error_lines = [line async for line in encode_ui_stream(broken_events(), message_id="message-3")]
    error_chunks = _chunks(error_lines)
    assert {"type": "error", "errorText": "stream failed"} in error_chunks
    assert error_chunks[-1] == {"type": "finish"}
    assert error_lines[-1] == "data: [DONE]\n\n"


async def test_ui_stream_encodes_adk_error_events():
    event = Event(
        author="zep_analyst",
        error_code="SAFETY",
        error_message="blocked by safety",
    )

    lines = [line async for line in encode_ui_stream(_events(event), message_id="message-4")]
    chunks = _chunks(lines)

    assert chunks == [
        {"type": "start", "messageId": "message-4"},
        {"type": "error", "errorText": "blocked by safety"},
        {"type": "finish"},
    ]
    assert lines[-1] == "data: [DONE]\n\n"
