"""
Basic Pydantic AI agent with Zep long-term memory.

This example wires Zep into a Pydantic AI agent using three pieces:

  - ``create_user`` /
    ``create_thread``         -- create the Zep resources one time and return
                                 the server-generated UUIDs.
  - ``ZepDeps``               -- carries the Zep client + the user, thread, and
                                 graph UUIDs.
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
import sys
import time

from pydantic_ai import Agent
from zep_cloud.client import AsyncZep

from zep_pydantic_ai import (
    ZepDeps,
    create_thread,
    create_user,
    create_zep_search_tool,
    zep_capabilities,
)

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise OSError("OPENAI_API_KEY is not set.")


async def wait_for_ingestion(
    zep: AsyncZep,
    graph_uuid: str,
    *,
    minimum_episodes: int = 2,
    timeout_seconds: int = 180,
    poll_seconds: float = 5.0,
) -> None:
    """Poll the graph until its episodes are processed, or the timeout ends.

    Zep ingests asynchronously, so a fact is not retrievable immediately.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pager = await zep.graph.episode.list(graph_uuid, limit=20)
        episodes = pager.items or []
        processed = [episode for episode in episodes if episode.processed]
        if len(processed) >= minimum_episodes:
            print(f"  {len(processed)} episodes are processed.\n")
            return
        await asyncio.sleep(poll_seconds)
    print("  The timeout ended before all episodes were processed.\n")


async def chat(agent: Agent, deps: ZepDeps, message: str) -> str:
    """Send one message through the agent; the assistant reply is persisted
    automatically by the after_run hook bundled in zep_capabilities(deps)."""
    result = await agent.run(message, deps=deps)
    return result.output


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    # Zep v4 addresses every resource by a server-generated UUID. A real
    # application creates the user and the thread one time, and stores the
    # UUIDs in its own database.
    #
    # Note: ZEPAI-3605 -- a user that is created without a ``user_id`` cannot
    # receive a thread message until the fix is deployed.
    user = await create_user(
        zep,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )
    thread = await create_thread(zep, user_uuid=user.uuid_)

    # ZepDeps must exist before the agent: zep_capabilities(deps) closes over
    # it to wire up automatic assistant persistence via Hooks(after_run=...).
    deps = ZepDeps(
        client=zep,
        user_uuid=user.uuid_,
        thread_uuid=thread.uuid_,
        graph_uuid=user.graph_uuid,
        first_name="Alice",
        last_name="Smith",
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
    print(f"  User UUID:   {user.uuid_}")
    print(f"  Thread UUID: {thread.uuid_}")
    print(f"  Graph UUID:  {user.graph_uuid}")
    print(f"{'=' * 60}\n")

    # Phase 1: seed some facts.
    print("--- Phase 1: Seeding facts ---\n")
    for msg in (
        "My name is Alice and I'm a software engineer.",
        "I live in Portland and love hiking on weekends.",
    ):
        print(f"User:  {msg}")
        print(f"Agent: {await chat(agent, deps, msg)}\n")

    # Phase 2: let Zep's asynchronous ingestion build the graph.
    print("--- Phase 2: Waiting for Zep graph processing ---\n")
    if user.graph_uuid:
        await wait_for_ingestion(zep, user.graph_uuid)

    # Phase 3: test memory recall in the same thread.
    print("--- Phase 3: Testing memory recall ---\n")
    checks = (
        ("What do I do for work?", ("engineer", "software")),
        ("Where do I live?", ("portland",)),
    )
    failures = []
    for msg, expected in checks:
        answer = await chat(agent, deps, msg)
        print(f"User:  {msg}")
        print(f"Agent: {answer}\n")
        if not any(word in answer.lower() for word in expected):
            failures.append(f"{msg!r} did not recall any of {expected}")

    # Phase 4: clean up the resources that this example created.
    print("--- Phase 4: Cleanup ---\n")
    await zep.user.delete(user.uuid_)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        sys.exit(1)

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
