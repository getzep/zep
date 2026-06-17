"""
Basic Pydantic AI agent with Zep long-term memory.

This example wires Zep into a Pydantic AI agent using three pieces:

  - ``ZepDeps``               -- carries the Zep client + user/thread identity.
  - ``zep_history_processor`` -- persists each user turn and injects Zep's
                                 context block, registered as a capability.
  - ``create_zep_search_tool``-- a model-callable graph-search tool.

The flow each turn:

  1. The history processor persists the user's message to Zep and prepends the
     retrieved context block to the prompt.
  2. The model answers (optionally calling ``zep_search``).
  3. ``persist_run`` stores the assistant's reply back to the Zep thread.

Prerequisites:
    pip install zep-pydantic-ai

    export ZEP_API_KEY="your-zep-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from zep_cloud.client import AsyncZep

from zep_pydantic_ai import (
    ZepDeps,
    create_zep_search_tool,
    persist_run,
    zep_history_processor,
)

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise OSError("OPENAI_API_KEY is not set.")

# Unique IDs for this demo run so repeated runs don't collide.
_suffix = uuid4().hex[:8]
USER_ID = f"pydantic-ai-example-user-{_suffix}"
THREAD_ID = f"pydantic-ai-example-thread-{_suffix}"


# One agent definition, reused across turns. Per-user identity lives in ZepDeps.
agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=ZepDeps,
    capabilities=[ProcessHistory(zep_history_processor)],
    tools=[create_zep_search_tool()],
    instructions=(
        "You are a helpful assistant with access to long-term memory. "
        "When context from Zep is injected into the prompt, use it to provide "
        "personalised, memory-aware responses. If you know something about the "
        "user from memory, reference it naturally. Use the zep_search tool when "
        "you need to look up specific details the user shared earlier."
    ),
)


async def chat(deps: ZepDeps, message: str) -> str:
    """Send one message through the agent and persist the assistant reply."""
    result = await agent.run(message, deps=deps)
    await persist_run(deps, result.new_messages())
    return result.output


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    deps = ZepDeps(
        client=zep,
        user_id=USER_ID,
        thread_id=THREAD_ID,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )

    print(f"\n{'=' * 60}")
    print("Pydantic AI + Zep Memory Example")
    print(f"{'=' * 60}")
    print(f"  User ID:   {USER_ID}")
    print(f"  Thread ID: {THREAD_ID}")
    print(f"{'=' * 60}\n")

    # Phase 1: seed some facts.
    print("--- Phase 1: Seeding facts ---\n")
    for msg in (
        "My name is Alice and I'm a software engineer.",
        "I live in Portland and love hiking on weekends.",
    ):
        print(f"User:  {msg}")
        print(f"Agent: {await chat(deps, msg)}\n")

    # Phase 2: let Zep's async ingestion build the graph.
    wait_seconds = 15
    print(f"--- Waiting {wait_seconds}s for Zep graph processing ---\n")
    await asyncio.sleep(wait_seconds)

    # Phase 3: test memory recall in the same thread.
    print("--- Phase 3: Testing memory recall ---\n")
    for msg in (
        "What do I do for work?",
        "Where do I live?",
    ):
        print(f"User:  {msg}")
        print(f"Agent: {await chat(deps, msg)}\n")

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
