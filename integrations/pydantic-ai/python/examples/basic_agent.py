"""
Basic Pydantic AI agent with Zep long-term memory.

This example wires Zep into a Pydantic AI agent using three pieces:

  - ``ZepDeps``               -- carries the Zep client + user/thread identity.
  - ``zep_capabilities``      -- bundles the history processor (persists each
                                 user turn and injects Zep's context block)
                                 with automatic assistant-reply persistence.
  - ``create_zep_search_tool``-- a model-callable graph-search tool.

The flow each turn:

  1. The history processor persists the user's message to Zep and prepends the
     retrieved context block to the prompt.
  2. The model answers (optionally calling ``zep_search``).
  3. The ``after_run`` hook (bundled by ``zep_capabilities``) automatically
     persists the assistant's reply back to the Zep thread -- no explicit
     ``persist_run`` call needed.

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
from zep_cloud.client import AsyncZep

from zep_pydantic_ai import ZepDeps, create_zep_search_tool, zep_capabilities

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


async def chat(agent: Agent, deps: ZepDeps, message: str) -> str:
    """Send one message through the agent; the assistant reply is persisted
    automatically by the after_run hook bundled in zep_capabilities(deps)."""
    result = await agent.run(message, deps=deps)
    return result.output


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    # ZepDeps must exist before the agent: zep_capabilities(deps) closes over
    # it to wire up automatic assistant persistence via Hooks(after_run=...).
    deps = ZepDeps(
        client=zep,
        user_id=USER_ID,
        thread_id=THREAD_ID,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )

    agent = Agent(
        "openai:gpt-5-mini",
        deps_type=ZepDeps,
        capabilities=zep_capabilities(deps),
        tools=[create_zep_search_tool()],
        instructions=(
            "You are a helpful assistant with access to long-term memory. "
            "When context from Zep is injected into the prompt, use it to provide "
            "personalised, memory-aware responses. If you know something about the "
            "user from memory, reference it naturally. Use the zep_search tool when "
            "you need to look up specific details the user shared earlier."
        ),
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
        print(f"Agent: {await chat(agent, deps, msg)}\n")

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
        print(f"Agent: {await chat(agent, deps, msg)}\n")

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
