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
from uuid import uuid4

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep

from zep_ms_agent_framework import ZepContextProvider

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

if not ZEP_API_KEY:
    raise SystemExit("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY is not set.")

# Unique identity for this demo run so repeated runs do not collide.
_suffix = uuid4().hex[:8]
USER_ID = f"af-example-user-{_suffix}"
THREAD_1 = f"af-example-thread1-{_suffix}"
THREAD_2 = f"af-example-thread2-{_suffix}"


def build_agent(zep: AsyncZep, thread_id: str) -> Agent:
    """Build an agent whose memory is scoped to USER_ID on the given thread."""
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
                user_id=USER_ID,
                thread_id=thread_id,
                first_name="Alice",
                last_name="Nguyen",
                email="alice@example.com",
            )
        ],
    )


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    print("=" * 64)
    print("Microsoft Agent Framework + Zep Memory Example")
    print("=" * 64)
    print(f"  User ID:   {USER_ID}")
    print(f"  Thread 1:  {THREAD_1}")
    print(f"  Thread 2:  {THREAD_2}")
    print("=" * 64, "\n")

    # --- Conversation 1: seed facts ----------------------------------------
    print("--- Conversation 1: seeding facts ---\n")
    agent1 = build_agent(zep, THREAD_1)
    seed_messages = [
        "Hi! I'm Alice, a data scientist living in Portland, Oregon.",
        "On weekends I love hiking and landscape photography.",
    ]
    for message in seed_messages:
        print(f"User:  {message}")
        result = await agent1.run(message)
        print(f"Agent: {result.text}\n")

    # --- Wait for asynchronous graph ingestion -----------------------------
    wait_seconds = 20
    print(f"--- Waiting {wait_seconds}s for Zep to process the graph ---\n")
    await asyncio.sleep(wait_seconds)

    # --- Conversation 2: recall in a brand-new thread ----------------------
    # A different thread for the SAME user proves recall comes from the user
    # graph (fused across threads), not from local conversation history.
    print("--- Conversation 2: memory recall in a new thread ---\n")
    agent2 = build_agent(zep, THREAD_2)
    recall_messages = [
        "What do I do for work, and where do I live?",
        "What are my hobbies?",
    ]
    for message in recall_messages:
        print(f"User:  {message}")
        result = await agent2.run(message)
        print(f"Agent: {result.text}\n")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
