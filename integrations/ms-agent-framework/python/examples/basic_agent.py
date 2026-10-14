"""
Basic Microsoft Agent Framework agent with Zep long-term memory.

This example wires a single ``Agent`` (driven by ``OpenAIChatClient``) to a
``ZepContextProvider`` and runs a short multi-turn conversation.  Earlier turns
seed facts about the user; a later turn -- in a *new* conversation thread --
shows the agent recalling those facts from Zep's user graph.

The provider persists every user and assistant turn to Zep and injects Zep's
Context Block into the model's instructions before each response.

Prerequisites:
    pip install zep-ms-agent-framework agent-framework-openai

    export ZEP_API_KEY="your-zep-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

from __future__ import annotations

import asyncio
import os
from typing import cast
from uuid import uuid4

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider, create_thread, create_user

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

if not ZEP_API_KEY:
    raise SystemExit("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY is not set.")

_suffix = uuid4().hex[:8]


def build_agent(zep: AsyncZep, user_uuid: str, thread_uuid: str) -> Agent:
    """Build an agent with memory scoped to the provided user and thread."""
    return Agent(
        OpenAIChatClient(model=OPENAI_MODEL, api_key=OPENAI_API_KEY),
        instructions=(
            "You are a helpful assistant with access to long-term memory. "
            "When context from Zep is provided, use it to give personalised, "
            "memory-aware answers. Be concise."
        ),
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_uuid=user_uuid,
                thread_uuid=thread_uuid,
                graph_uuid=user_uuid,
                user_message_name="Alice Nguyen",
            )
        ],
    )


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    user_uuid: str | None = None
    thread_uuids: list[str] = []

    try:
        user = await create_user(
            zep,
            first_name="Alice",
            last_name="Nguyen",
            email=f"alice-{_suffix}@example.com",
        )
        user_uuid = cast(str, user.uuid_)
        first_thread = await create_thread(zep, user_uuid=user_uuid)
        first_thread_uuid = cast(str, first_thread.uuid_)
        thread_uuids.append(first_thread_uuid)

        print("Microsoft Agent Framework + Zep Memory Example")
        print(f"User UUID: {user_uuid}")
        print(f"Thread UUID: {first_thread_uuid}\n")

        print("--- Conversation 1: seeding facts ---\n")
        agent1 = build_agent(zep, user_uuid, first_thread_uuid)
        seed_messages = [
            "Hi! I am a data scientist living in Portland, Oregon.",
            "On weekends I love hiking and landscape photography.",
        ]
        for message in seed_messages:
            print(f"User:  {message}")
            result = await agent1.run(message)
            print(f"Agent: {result.text}\n")

        wait_seconds = 20
        print(f"Waiting {wait_seconds}s for Zep to process the graph.\n")
        await asyncio.sleep(wait_seconds)

        second_thread = await create_thread(zep, user_uuid=user_uuid)
        second_thread_uuid = cast(str, second_thread.uuid_)
        thread_uuids.append(second_thread_uuid)

        print("--- Conversation 2: recall in a new thread ---\n")
        agent2 = build_agent(zep, user_uuid, second_thread_uuid)
        for message in [
            "What do I do for work, and where do I live?",
            "What are my hobbies?",
        ]:
            print(f"User:  {message}")
            result = await agent2.run(message)
            print(f"Agent: {result.text}\n")
    finally:
        cleanup_errors: list[Exception] = []
        for thread_uuid in thread_uuids:
            try:
                await zep.thread.delete(thread_uuid)
            except Exception as exc:
                cleanup_errors.append(exc)
        if user_uuid is not None:
            try:
                await zep.user.delete(user_uuid)
            except Exception as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise RuntimeError(
                "Failed to delete one or more example resources"
            ) from cleanup_errors[0]


if __name__ == "__main__":
    asyncio.run(main())
