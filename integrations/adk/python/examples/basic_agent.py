"""
Basic Google ADK agent with Zep long-term memory.

This example shows the shared-agent pattern: one Agent definition is created
once and shared across all users.  Per-user identity (user ID, name, email)
is passed via ADK session state.

ZepContextTool and create_after_model_callback work together so that:

  - User messages are persisted to Zep on every turn.
  - Relevant context from Zep's knowledge graph is injected into prompts.
  - Assistant responses are persisted to Zep after each model call.

The Zep user and thread are provisioned explicitly, out-of-band, via
``ensure_user`` and ``ensure_thread`` -- before the agent runs its first
turn.  ``ensure_user``'s ``on_created`` hook demonstrates one-time per-user
setup (here, seeding a user summary instruction) that only runs when the
user is genuinely new.

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

from zep_adk import ZepContextTool, create_after_model_callback, ensure_thread, ensure_user

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")  # noqa: F841

if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")
if not GOOGLE_API_KEY:
    raise OSError("GOOGLE_API_KEY is not set.")

# Generate unique IDs for this demo session
_suffix = uuid4().hex[:8]
USER_ID = f"adk-example-user-{_suffix}"
SESSION_ID = f"adk-example-session-{_suffix}"
SESSION_ID_2 = f"adk-example-session-2-{_suffix}"
APP_NAME = "zep-adk-example"


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


async def setup_new_user(zep: AsyncZep, user_id: str) -> None:
    """One-time setup for a newly created Zep user.

    Fires only when ``ensure_user`` actually creates the user -- never for a
    user that already existed.  This is the place to configure per-user
    ontology, custom instructions, or (as here) a user summary instruction.
    """
    print(f"  [on_created] New Zep user {user_id} -- seeding summary instructions.")
    await zep.user.add_user_summary_instructions(
        instructions=[
            UserInstruction(
                name="professional-background",
                text=(
                    "Summarize this user's professional background, interests, "
                    "and living situation in a concise paragraph."
                ),
            )
        ],
        user_ids=[user_id],
    )


async def main() -> None:
    zep_client = AsyncZep(api_key=ZEP_API_KEY)

    print(f"\n{'=' * 60}")
    print("ADK + Zep Memory Example (Shared-Agent Pattern)")
    print(f"{'=' * 60}")
    print(f"  User ID:    {USER_ID}")
    print(f"  Session ID: {SESSION_ID}")
    print(f"{'=' * 60}\n")

    # --- Provision the Zep user and thread out-of-band, before the first
    # turn.  This replaces the old lazy in-band creation: the agent's turn
    # path (ZepContextTool) never creates users or threads itself.
    print("--- Provisioning Zep user + thread ---\n")
    await ensure_user(
        zep_client,
        user_id=USER_ID,
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
        on_created=setup_new_user,
    )
    await ensure_thread(zep_client, thread_id=SESSION_ID, user_id=USER_ID)

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
    #       results = await ctx.zep.graph.search(
    #           user_id=ctx.user_id, query=ctx.user_message, scope="edges"
    #       )
    #       return "\n".join(e.fact for e in results.edges or [])
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
    # user_id is automatically used as the Zep user ID.
    # session_id is automatically used as the Zep thread ID.
    # Only the display name needs to be in state; email goes to ensure_user.
    session_state = {
        "zep_first_name": "Alice",
        "zep_last_name": "Smith",
    }
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
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
        response = await send_message(runner, SESSION_ID, USER_ID, msg)
        print(f"Agent: {response}\n")

    # Phase 2: Wait for Zep graph processing
    wait_seconds = 15
    print(f"--- Waiting {wait_seconds}s for Zep graph processing ---\n")
    await asyncio.sleep(wait_seconds)

    # Phase 3: Test memory recall
    print("--- Phase 3: Testing memory recall ---\n")
    recall_messages = [
        "What do I do for work?",
        "Where do I live?",
    ]
    for msg in recall_messages:
        print(f"User:  {msg}")
        response = await send_message(runner, SESSION_ID, USER_ID, msg)
        print(f"Agent: {response}\n")

    # Phase 4: Cross-thread recall -- a brand-new thread for the same user.
    # Facts are fused into the user's graph (not the thread), so a second,
    # never-before-seen thread can recall them immediately.
    print("--- Phase 4: Cross-thread recall (new thread, same user) ---\n")
    await ensure_thread(zep_client, thread_id=SESSION_ID_2, user_id=USER_ID)
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID_2,
        state=session_state,
    )
    msg = "What do you know about me?"
    print(f"User:  {msg}")
    response = await send_message(runner, SESSION_ID_2, USER_ID, msg)
    print(f"Agent: {response}\n")

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
