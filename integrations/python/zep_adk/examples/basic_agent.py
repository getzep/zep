"""
Basic Google ADK agent with Zep long-term memory.

This example shows the shared-agent pattern: one Agent definition is created
once and shared across all users.  Per-user identity (user ID, name, email)
is passed via ADK session state.

ZepContextTool and create_after_model_callback work together so that:

  - User messages are persisted to Zep on every turn.
  - Relevant context from Zep's knowledge graph is injected into prompts.
  - Assistant responses are persisted to Zep after each model call.

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
from zep_cloud.client import AsyncZep

from zep_adk import ZepContextTool, create_after_model_callback

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


async def main() -> None:
    zep_client = AsyncZep(api_key=ZEP_API_KEY)

    print(f"\n{'=' * 60}")
    print("ADK + Zep Memory Example (Shared-Agent Pattern)")
    print(f"{'=' * 60}")
    print(f"  User ID:    {USER_ID}")
    print(f"  Session ID: {SESSION_ID}")
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

    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    # --- Per-user session (identity in state) ---
    # user_id is automatically used as the Zep user ID.
    # session_id is automatically used as the Zep thread ID.
    # Only name/email need to be in state.
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
        state={
            "zep_first_name": "Alice",
            "zep_last_name": "Smith",
            "zep_email": "alice@example.com",
        },
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

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
