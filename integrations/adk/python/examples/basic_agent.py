"""
Basic Google ADK agent with Zep long-term memory.

This example shows the shared-agent pattern: one Agent definition is created
once and shared across all users.  Per-user identity (the Zep user UUID, the
Zep thread UUID, and the display name) is passed through ADK session state.

ZepContextTool and create_after_model_callback work together so that:

  - User messages are persisted to Zep on every turn.
  - Relevant context from Zep's knowledge graph is injected into prompts.
  - Assistant responses are persisted to Zep after each model call.

Zep v4 addresses every user, thread, and graph by a server-generated UUID.
The example creates the user and the thread out-of-band with ``create_user``
and ``create_thread``, and it keeps the returned UUIDs.  A production
application stores the same UUIDs in its own database.

Prerequisites:
    pip install zep-adk

    export GOOGLE_API_KEY="your-google-api-key"
    export ZEP_API_KEY="your-zep-api-key"
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from zep_cloud import UserInstruction
from zep_cloud.client import AsyncZep

from zep_adk import ZepContextTool, create_after_model_callback, create_thread, create_user

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")  # noqa: F841

if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")
if not GOOGLE_API_KEY:
    raise OSError("GOOGLE_API_KEY is not set.")

APP_NAME = "zep-adk-example"

# A human-readable label for the Zep user.  It is not an address: every call
# below addresses the user by the UUID that Zep generates.  The label is a
# temporary workaround for a production v4 defect: the API rejects
# `thread.add_messages` with a 404 for a user that has no `user_id`.  Remove the
# label after the defect is corrected.
USER_NAME_ID = f"adk-example-user-{uuid4().hex[:8]}"


async def send_message(runner: Runner, session_id: str, user_id: str, text: str) -> str:
    """Send a message to the agent and collect the text response."""
    content = types.Content(role="user", parts=[types.Part(text=text)])

    response_parts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_parts.append(part.text)

    return " ".join(response_parts).strip()


async def setup_new_user(zep: AsyncZep, user_uuid: str) -> None:
    """One-time setup for a new Zep user.

    This is the place to configure a per-user ontology, custom instructions,
    or (as here) a user summary instruction.
    """
    print(f"  [setup] New Zep user {user_uuid} -- seeding summary instructions.")
    await zep.user.set_summary_instructions(
        user_uuid,
        inherited=False,
        instructions=[
            UserInstruction(
                name="professional-background",
                text=(
                    "Summarize this user's professional background, interests, "
                    "and living situation in a concise paragraph."
                ),
            )
        ],
    )


async def main() -> None:
    zep_client = AsyncZep(api_key=ZEP_API_KEY)

    # --- Provision the Zep user and thread out-of-band, before the first
    # turn.  The agent's turn path (ZepContextTool) never creates users or
    # threads itself.  Zep generates the UUIDs; the application stores them.
    print("--- Provisioning the Zep user and thread ---\n")
    user = await create_user(
        zep_client,
        user_id=USER_NAME_ID,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
    )
    await setup_new_user(zep_client, user.uuid_)
    thread = await create_thread(zep_client, user_uuid=user.uuid_)

    print(f"\n{'=' * 60}")
    print("ADK + Zep Memory Example (Shared-Agent Pattern)")
    print(f"{'=' * 60}")
    print(f"  User UUID:   {user.uuid_}")
    print(f"  Graph UUID:  {user.graph_uuid}")
    print(f"  Thread UUID: {thread.uuid_}")
    print(f"{'=' * 60}\n")

    # --- One-time agent setup (shared across all users) ---
    agent = Agent(
        name="zep_memory_agent",
        model="gemini-2.5-flash",
        description=(
            "A helpful assistant with Zep-powered long-term memory. "
            "Remembers facts about the user across conversations."
        ),
        instruction=(
            "You are a helpful assistant with access to long-term memory. "
            "When context from Zep is injected into the prompt, use it to "
            "provide personalised, memory-aware responses. "
            "If you know something about the user from memory, reference it "
            "naturally."
        ),
        tools=[ZepContextTool(zep_client=zep_client)],
        after_model_callback=create_after_model_callback(zep_client=zep_client),
    )
    # To customise how context is retrieved (e.g. a filtered search or a
    # different graph) instead of the default `add_messages(return_context=True)`
    # round-trip, pass a `context_builder` to `ZepContextTool`:
    #
    #   async def my_builder(ctx: ContextInput) -> str | None:
    #       user = await ctx.zep.user.get(ctx.user_uuid)
    #       pager = await ctx.zep.graph.search_edges(
    #           user.graph_uuid, query=ctx.user_message
    #       )
    #       return "\n".join(e.fact for e in pager.items or [] if e.fact)
    #
    #   ZepContextTool(zep_client=zep_client, context_builder=my_builder)
    #
    # See the `ContextInput` docstring in `zep_adk.context_tool` for the full
    # contract (error isolation, concurrency with persistence).

    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # --- Per-user session (identity in state) ---
    # The Zep UUIDs travel in session state.  The display name lets Zep
    # resolve identity in the graph; the email goes to `create_user`.
    session_state = {
        "zep_user_uuid": user.uuid_,
        "zep_thread_uuid": thread.uuid_,
        "zep_graph_uuid": user.graph_uuid,
        "zep_first_name": "Alice",
        "zep_last_name": "Smith",
    }
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user.uuid_,
        session_id=thread.uuid_,
        state=session_state,
    )

    # Phase 1: Seed some facts
    print("--- Phase 1: Seeding facts ---\n")
    seed_messages = [
        "My name is Alice and I'm a software engineer.",
        "I live in Portland and love hiking on weekends.",
    ]
    for msg in seed_messages:
        print(f"User:  {msg}")
        response = await send_message(runner, thread.uuid_, user.uuid_, msg)
        print(f"Agent: {response}\n")

    # Phase 2: Wait for Zep graph processing.  Ingestion is asynchronous, so
    # poll the episodes of the user graph until Zep has processed them.
    print("--- Phase 2: Waiting for Zep graph processing ---\n")
    await wait_for_episodes(zep_client, user.graph_uuid)

    # Phase 3: Test memory recall
    print("--- Phase 3: Testing memory recall ---\n")
    recall_messages = [
        "What do I do for work?",
        "Where do I live?",
    ]
    for msg in recall_messages:
        print(f"User:  {msg}")
        response = await send_message(runner, thread.uuid_, user.uuid_, msg)
        print(f"Agent: {response}\n")

    # Phase 4: Cross-thread recall -- a brand-new thread for the same user.
    # Facts are fused into the user's graph (not the thread), so a second,
    # never-before-seen thread can recall them immediately.
    print("--- Phase 4: Cross-thread recall (new thread, same user) ---\n")
    thread_2 = await create_thread(zep_client, user_uuid=user.uuid_)
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user.uuid_,
        session_id=thread_2.uuid_,
        state={**session_state, "zep_thread_uuid": thread_2.uuid_},
    )
    msg = "What do you know about me?"
    print(f"User:  {msg}")
    response = await send_message(runner, thread_2.uuid_, user.uuid_, msg)
    print(f"Agent: {response}\n")

    print("Done!")


async def wait_for_episodes(
    zep: AsyncZep,
    graph_uuid: str,
    timeout_seconds: float = 180.0,
    poll_interval: float = 3.0,
) -> None:
    """Poll the episodes of a graph until Zep has processed all of them."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        pager = await zep.graph.episode.list(graph_uuid, limit=20)
        episodes = pager.items or []
        if episodes and all(episode.processed for episode in episodes):
            print(f"  All {len(episodes)} episodes are processed.\n")
            return
        unprocessed = [episode for episode in episodes if not episode.processed]
        print(f"  {len(unprocessed)} of {len(episodes)} episodes are still in process...")
        await asyncio.sleep(poll_interval)
    print("  Timed out while the episodes were in process. The example continues.\n")


if __name__ == "__main__":
    asyncio.run(main())
