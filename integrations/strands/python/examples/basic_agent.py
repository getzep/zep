"""
Basic Strands Agents agent with Zep long-term memory via MemoryManager.

This example wires a ``ZepMemoryStore`` into Strands' ``MemoryManager`` so the
agent automatically:

* injects relevant Zep context before each model call
* extracts conversation turns into the user graph (server-side, via
  ``add_messages``) on the manager's default cadence

Earlier turns seed facts about the user; a later turn -- in a *new* conversation
thread -- shows the agent recalling those facts from Zep's user graph.

Zep v4 addresses a user and a thread by a server-generated UUID. The example
creates the user and the threads one time, and keeps the UUIDs. A real
application stores the UUIDs in its own database.

Prerequisites:
    pip install zep-strands 'strands-agents[openai]'

    export ZEP_API_KEY="your-zep-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from importlib import import_module
from typing import Any

import openai
from strands import Agent
from strands.memory import MemoryManager
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.content import Message
from zep_cloud.client import AsyncZep

from zep_strands import ZepMemoryStore, create_thread, create_user


def _message_text(message: Message | object) -> str:
    """Extract joined text blocks from an agent result message."""
    if not isinstance(message, dict):
        return str(message)
    parts = [
        block["text"]
        for block in message.get("content") or []
        if isinstance(block, dict) and "text" in block
    ]
    return "\n".join(parts) if parts else str(message)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")

if not ZEP_API_KEY:
    raise SystemExit("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY is not set.")


def _parse_sse_chunk(buffer: bytes, chunk: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    lines = (buffer + chunk).split(b"\n")
    remainder = lines.pop()
    events = []
    for line in lines:
        line = line.rstrip(b"\r")
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return remainder, events


def _make_tee_stream(
    stream: Any,
    async_byte_stream: Any,
    on_chunk: Callable[[bytes], None],
    on_end: Callable[[], None],
) -> Any:
    def initialize(
        self: Any,
        original: Any,
        chunk_callback: Callable[[bytes], None],
        end_callback: Callable[[], None],
    ) -> None:
        self._original = original
        self._on_chunk = chunk_callback
        self._on_end = end_callback

    async def iterate(self: Any) -> AsyncIterator[bytes]:
        async for chunk in self._original:
            self._on_chunk(chunk)
            yield chunk
        self._on_end()

    async def close(self: Any) -> None:
        await self._original.aclose()

    tee_type = type(
        "TeeAsyncByteStream",
        (async_byte_stream,),
        {"__init__": initialize, "__aiter__": iterate, "aclose": close},
    )
    return tee_type(stream, on_chunk, on_end)


class ReasoningReplayResponsesModel(OpenAIResponsesModel):
    """Replay encrypted reasoning items before their matching tool calls."""

    def _resolve_client_args(self) -> dict[str, Any]:
        client_args = super()._resolve_client_args()
        if "http_client" in client_args:
            return client_args
        return {
            **client_args,
            "http_client": openai.DefaultAsyncHttpxClient(
                event_hooks={"response": [self._capture_response]}
            ),
        }

    async def _capture_response(self, response: Any) -> None:
        pending: list[dict[str, Any]] = []
        buffer = b""

        def on_chunk(chunk: bytes) -> None:
            nonlocal buffer
            buffer, events = _parse_sse_chunk(buffer, chunk)
            for event in events:
                self._capture_reasoning_event(event, pending)

        def on_end() -> None:
            nonlocal buffer
            if buffer:
                buffer, events = _parse_sse_chunk(buffer, b"\n")
                for event in events:
                    self._capture_reasoning_event(event, pending)

        module_name = type(response).__module__.split(".", maxsplit=1)[0]
        async_byte_stream = vars(import_module(module_name))["AsyncByteStream"]
        response.stream = _make_tee_stream(response.stream, async_byte_stream, on_chunk, on_end)

    def _capture_reasoning_event(
        self, event: dict[str, Any], pending: list[dict[str, Any]]
    ) -> None:
        if event.get("type") != "response.output_item.done":
            return
        item = event.get("item")
        if not isinstance(item, dict):
            return
        if item.get("type") == "reasoning":
            keys = ("type", "id", "summary", "encrypted_content", "content")
            pending.append({key: item[key] for key in keys if item.get(key) is not None})
        elif item.get("type") == "function_call":
            call_id = item.get("call_id")
            if pending and call_id:
                reasoning_by_call_id = getattr(self, "_reasoning_by_call_id", {})
                reasoning_by_call_id[call_id] = list(pending)
                self._reasoning_by_call_id = reasoning_by_call_id
            pending.clear()
        elif item.get("type") == "message":
            pending.clear()

    def _format_request(self, messages: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super()._format_request(messages, *args, **kwargs)
        input_items = request.get("input")
        if not isinstance(input_items, list):
            return request

        reasoning_by_call_id = getattr(self, "_reasoning_by_call_id", {})
        existing_reasoning_ids = {
            item.get("id")
            for item in input_items
            if isinstance(item, dict) and item.get("type") == "reasoning"
        }
        replayed_input = []
        for item in input_items:
            if isinstance(item, dict) and item.get("type") == "function_call":
                for reasoning in reasoning_by_call_id.get(item.get("call_id"), []):
                    reasoning_id = reasoning.get("id")
                    if reasoning_id not in existing_reasoning_ids:
                        replayed_input.append(reasoning)
                        existing_reasoning_ids.add(reasoning_id)
            replayed_input.append(item)
        request["input"] = replayed_input
        return request


def build_model() -> OpenAIResponsesModel:
    return ReasoningReplayResponsesModel(
        client_args={"api_key": OPENAI_API_KEY},
        model_id=OPENAI_MODEL,
        params={"reasoning": {"effort": "low"}, "include": ["reasoning.encrypted_content"]},
    )


async def build_agent(zep: AsyncZep, user_uuid: str, thread_uuid: str) -> Agent:
    """Build an agent whose memory is scoped to the user on the given thread."""
    store = ZepMemoryStore(
        zep_client=zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        first_name="Alice",
        last_name="Nguyen",
        writable=True,
        extraction=True,
        expose_search_tool=True,
        search_pinned_params={"scope": "auto"},
    )
    return Agent(
        model=build_model(),
        system_prompt=(
            "You are a helpful assistant with access to long-term memory. "
            "When memory context is provided, use it to give personalised, "
            "memory-aware answers. Be concise."
        ),
        memory_manager=MemoryManager(stores=[store], add_tool_config=True),
    )


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    print("=" * 64)
    print("Strands Agents + Zep MemoryStore Example")
    print("=" * 64)
    user = await create_user(
        zep,
        first_name="Alice",
        last_name="Nguyen",
        email="alice@example.com",
    )
    if user.uuid_ is None:
        raise SystemExit("Zep did not return a user UUID.")
    thread_1 = await create_thread(zep, user_uuid=user.uuid_)
    thread_2 = await create_thread(zep, user_uuid=user.uuid_)
    if thread_1.uuid_ is None or thread_2.uuid_ is None:
        raise SystemExit("Zep did not return a thread UUID.")

    print(f"  User UUID:      {user.uuid_}")
    print(f"  Thread 1 UUID:  {thread_1.uuid_}")
    print(f"  Thread 2 UUID:  {thread_2.uuid_}")
    print("=" * 64, "\n")

    print("--- Conversation 1: seeding facts ---\n")
    agent1 = await build_agent(zep, user.uuid_, thread_1.uuid_)
    seed_messages = [
        "Hi! I'm Alice, a data scientist living in Portland, Oregon.",
        "On weekends I love hiking and landscape photography.",
    ]
    for message in seed_messages:
        print(f"User:  {message}")
        result = await agent1.invoke_async(message)
        print(f"Agent: {_message_text(result.message)}\n")

    # Flush at the session boundary: invoke_async does not flush, and flushing
    # every turn would defeat the extraction trigger's schedule.
    if agent1.memory_manager is not None:
        await agent1.memory_manager.flush()

    wait_seconds = 20
    print(f"--- Waiting {wait_seconds}s for Zep to process the graph ---\n")
    await asyncio.sleep(wait_seconds)

    print("--- Conversation 2: recall in a brand-new thread ---\n")
    agent2 = await build_agent(zep, user.uuid_, thread_2.uuid_)
    recall = "Where do I live, and what do I like to do on weekends?"
    print(f"User:  {recall}")
    result = await agent2.invoke_async(recall)
    print(f"Agent: {_message_text(result.message)}\n")


if __name__ == "__main__":
    asyncio.run(main())
