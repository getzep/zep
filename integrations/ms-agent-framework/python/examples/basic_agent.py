"""
Basic Microsoft Agent Framework agent with Zep long-term memory.

This example wires a single ``Agent`` (driven by ``OpenAIChatClient``) to a
``ZepContextProvider`` and runs a short multi-turn conversation.  Earlier turns
seed facts about the user; a later turn -- in a *new* conversation thread --
shows the agent recalling those facts from Zep's user graph.

The provider persists every user and assistant turn to Zep and injects Zep's
Context Block into the model's instructions before each response.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
The example creates the user and the two threads one time, reads the UUIDs
from the create responses, and gives the UUIDs to the provider.  An
application stores the same UUIDs in its own database.

Prerequisites:
    pip install zep-ms-agent-framework agent-framework-openai

    export ZEP_API_KEY="your-zep-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

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


def build_agent(zep: AsyncZep, user_uuid: str, thread_uuid: str, graph_uuid: str) -> Agent:
    """Build an agent whose memory is scoped to the given user and thread."""
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
                graph_uuid=graph_uuid,
            )
        ],
    )


async def wait_for_ingestion(
    zep: AsyncZep,
    graph_uuid: str,
    timeout_seconds: float = 180.0,
    poll_interval: float = 3.0,
) -> None:
    """Poll the graph episodes until Zep processes all of them.

    Ingestion in Zep is asynchronous. A fact is retrievable only after Zep
    processes the episode that carries it.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout_seconds:
        pager = await zep.graph.episode.list(graph_uuid, limit=20)
        episodes = pager.items or []
        if episodes and all(episode.processed for episode in episodes):
            print(f"Zep processed {len(episodes)} episodes.\n")
            return
        await asyncio.sleep(poll_interval)
    print("Timed out while the example waited for episode processing.\n")


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    # --- One-time provisioning: the server returns the UUIDs ---------------
    # The user_id label is a temporary workaround for a production v4 defect:
    # for a user with no label, thread.add_messages and thread.get_context
    # give a 404. The label is not used for addressing. The example addresses
    # every resource by the UUID that the server returns.
    user = await create_user(
        zep,
        user_id=f"af-example-{uuid.uuid4().hex[:8]}",
        first_name="Alice",
        last_name="Nguyen",
        email="alice@example.com",
    )
    user_uuid = str(user.uuid_)
    graph_uuid = str(user.graph_uuid)
    thread_1 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)
    thread_2 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)

    print("=" * 64)
    print("Microsoft Agent Framework + Zep Memory Example")
    print("=" * 64)
    print(f"  User UUID:   {user_uuid}")
    print(f"  Graph UUID:  {graph_uuid}")
    print(f"  Thread 1:    {thread_1}")
    print(f"  Thread 2:    {thread_2}")
    print("=" * 64, "\n")

    # --- Conversation 1: seed facts ----------------------------------------
    print("--- Conversation 1: seeding facts ---\n")
    agent1 = build_agent(zep, user_uuid, thread_1, graph_uuid)
    seed_messages = [
        "Hi! I'm Alice, a data scientist living in Portland, Oregon.",
        "On weekends I love hiking and landscape photography.",
    ]
    for message in seed_messages:
        print(f"User:  {message}")
        result = await agent1.run(message)
        print(f"Agent: {result.text}\n")

    # --- Wait for asynchronous graph ingestion -----------------------------
    print("--- Waiting for Zep to process the graph ---\n")
    await wait_for_ingestion(zep, graph_uuid)

    # --- Conversation 2: recall in a brand-new thread ----------------------
    # A different thread for the SAME user proves recall comes from the user
    # graph (fused across threads), not from local conversation history.
    print("--- Conversation 2: memory recall in a new thread ---\n")
    agent2 = build_agent(zep, user_uuid, thread_2, graph_uuid)
    recall_messages = [
        "What do I do for work, and where do I live?",
        "What are my hobbies?",
    ]
    answers: list[str] = []
    for message in recall_messages:
        print(f"User:  {message}")
        result = await agent2.run(message)
        print(f"Agent: {result.text}\n")
        answers.append(result.text.lower())

    # --- Verify the recall --------------------------------------------------
    transcript = " ".join(answers)
    keywords = ["data scientist", "portland", "hiking", "photography"]
    recalled = [keyword for keyword in keywords if keyword in transcript]
    print(f"Recalled keywords: {recalled}")
    if not recalled:
        raise SystemExit("The agent did not recall any seeded fact.")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
