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
import os
from uuid import uuid4

from strands import Agent
from strands.memory import MemoryManager
from strands.models.openai import OpenAIModel
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
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

if not ZEP_API_KEY:
    raise SystemExit("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY is not set.")

_suffix = uuid4().hex[:8]
USER_NAME = f"strands-example-user-{_suffix}"


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
        model=OpenAIModel(client_args={"api_key": OPENAI_API_KEY}, model_id=OPENAI_MODEL),
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
        # Temporary workaround for a production v4 defect: thread.add_messages
        # returns 404 when the user has no user_id. The label is not an address.
        user_id=USER_NAME,
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
