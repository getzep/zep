"""Encode Google ADK events as Vercel AI SDK UI message stream chunks."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from google.adk.events import Event


def _sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'), ensure_ascii=False)}\n\n"


async def encode_ui_stream(events: AsyncIterator[Event], *, message_id: str) -> AsyncIterator[str]:
    """Convert ADK events to the Vercel AI SDK UI message stream protocol."""
    yield _sse({"type": "start", "messageId": message_id})
    seen_calls: set[str] = set()
    step_number = 0
    step_open = False
    text_id: str | None = None
    text_open = False
    partial_text = False

    try:
        async for event in events:
            content = event.content
            parts = content.parts if content else []
            function_responses = event.get_function_responses()
            if function_responses:
                for function_response in function_responses:
                    response = function_response.response
                    if isinstance(response, dict) and set(response) == {"result"}:
                        response = response["result"]
                    yield _sse(
                        {
                            "type": "tool-output-available",
                            "toolCallId": function_response.id,
                            "output": response,
                        }
                    )
                continue

            is_model_event = bool(
                content
                and content.role == "model"
                and (
                    any(getattr(part, "text", None) for part in parts) or event.get_function_calls()
                )
            )
            if not is_model_event:
                continue
            if not step_open:
                step_number += 1
                yield _sse({"type": "start-step"})
                step_open = True

            text = "".join(
                part.text
                for part in parts
                if getattr(part, "text", None) and not getattr(part, "thought", False)
            )
            if event.partial:
                if text:
                    if not text_open:
                        text_id = f"{message_id}-text-{step_number}"
                        yield _sse({"type": "text-start", "id": text_id})
                        text_open = True
                    yield _sse({"type": "text-delta", "id": text_id, "delta": text})
                    partial_text = True
            else:
                if text and not partial_text:
                    text_id = f"{message_id}-text-{step_number}"
                    yield _sse({"type": "text-start", "id": text_id})
                    yield _sse({"type": "text-delta", "id": text_id, "delta": text})
                    text_open = True
                if text_open:
                    yield _sse({"type": "text-end", "id": text_id})
                    text_open = False
                    text_id = None

                for function_call in event.get_function_calls():
                    call_id = function_call.id
                    if not call_id or call_id in seen_calls:
                        continue
                    seen_calls.add(call_id)
                    yield _sse(
                        {
                            "type": "tool-input-start",
                            "toolCallId": call_id,
                            "toolName": function_call.name,
                        }
                    )
                    yield _sse(
                        {
                            "type": "tool-input-available",
                            "toolCallId": call_id,
                            "toolName": function_call.name,
                            "input": function_call.args or {},
                        }
                    )
                yield _sse({"type": "finish-step"})
                step_open = False
                partial_text = False
    except Exception as exc:  # noqa: BLE001 - encode run errors for the UI
        if text_open:
            yield _sse({"type": "text-end", "id": text_id})
        if step_open:
            yield _sse({"type": "finish-step"})
        yield _sse({"type": "error", "errorText": str(exc)})
    else:
        if text_open:
            yield _sse({"type": "text-end", "id": text_id})
        if step_open:
            yield _sse({"type": "finish-step"})

    yield _sse({"type": "finish"})
    yield "data: [DONE]\n\n"
