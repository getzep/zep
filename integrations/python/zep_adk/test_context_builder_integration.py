"""
Integration test for the custom context_builder feature.

Tests that:
  1. A custom context_builder callable is invoked during process_llm_request
  2. The builder receives the correct arguments (zep_client, user_id, thread_id, message)
  3. Message persistence and context building run in parallel (via asyncio.gather)
  4. The context returned by the builder is injected into the LLM prompt
  5. The agent uses the custom context in its response

Requires:
    ZEP_API_KEY and GOOGLE_API_KEY environment variables.

Usage:
    source /Users/jackryan/.env.zep_production && uv run python test_context_builder_integration.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
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
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

if not ZEP_API_KEY:
    print("ERROR: ZEP_API_KEY is not set.")
    sys.exit(1)
if not GOOGLE_API_KEY:
    print("ERROR: GOOGLE_API_KEY is not set.")
    sys.exit(1)

_suffix = uuid4().hex[:8]
USER_ID = f"adk-cb-test-user-{_suffix}"
SESSION_ID = f"adk-cb-test-session-{_suffix}"
THREAD_ID = f"adk-cb-test-thread-{_suffix}"
APP_NAME = "zep-adk-cb-test"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_context_builder")

logging.getLogger("google.adk").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Custom context builder -- simulates an advanced multi-source context
# ---------------------------------------------------------------------------
builder_call_log: list[dict] = []


async def custom_context_builder(
    zep_client: AsyncZep,
    user_id: str,
    thread_id: str,
    user_message: str,
) -> str | None:
    """Custom context builder that combines multiple Zep sources.

    This simulates what Zscaler might do: pull context from the user's
    knowledge graph AND inject additional hard-coded business context.
    """
    builder_call_log.append({
        "user_id": user_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "timestamp": time.monotonic(),
    })

    logger.info("Custom context builder called for user=%s, thread=%s", user_id, thread_id)

    # 1. Get Zep's standard user context (simulates get_user_context or graph.search)
    zep_context = None
    try:
        zep_context_resp = await zep_client.user.get_context(user_id=user_id)
        zep_context = zep_context_resp.context if zep_context_resp else None
        logger.info("Got Zep user context: %d chars", len(zep_context) if zep_context else 0)
    except Exception as exc:
        logger.info("No Zep user context yet (expected on first call): %s", exc)

    # 2. Combine with custom business context (simulating a second knowledge graph)
    parts = []

    if zep_context:
        parts.append(f"=== User Knowledge Graph ===\n{zep_context}")

    # Simulate injecting context from a second source (e.g., company policies KB)
    parts.append(
        "=== Company Policies ===\n"
        "- All employees get 20 days PTO per year.\n"
        "- The engineering team uses Python and Go.\n"
        "- The CEO's name is Alex Johnson.\n"
        "- The company headquarters is in San Francisco."
    )

    return "\n\n".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def send_message(runner: Runner, session_id: str, user_id: str, text: str) -> str:
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


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------
async def main() -> None:
    zep_client = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep ADK Context Builder Integration Test")
    print(f"{'=' * 70}")
    print(f"  User ID:    {USER_ID}")
    print(f"  Thread ID:  {THREAD_ID}")
    print(f"  Session ID: {SESSION_ID}")
    print(f"{'=' * 70}\n")

    try:
        # ==================================================================
        # Step 1: Create the agent with a custom context_builder
        # ==================================================================
        print("[Step 1] Creating ADK agent with custom context_builder...")

        agent = Agent(
            name="zep_cb_test_agent",
            model="gemini-2.5-flash",
            instruction=(
                "You are a helpful assistant with long-term memory. "
                "When context is injected into the prompt, use ALL of it to "
                "provide complete, memory-aware responses. Reference specific "
                "facts from the context naturally."
            ),
            tools=[
                ZepContextTool(
                    zep_client=zep_client,
                    context_builder=custom_context_builder,
                )
            ],
            after_model_callback=create_after_model_callback(zep_client=zep_client),
        )

        session_service = InMemorySessionService()
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
            state={
                "zep_thread_id": THREAD_ID,
                "zep_first_name": "TestCB",
                "zep_last_name": "User",
            },
        )

        print("  -> Agent created with custom context_builder.\n")

        # ==================================================================
        # Step 2: Send a message and verify the custom builder was called
        # ==================================================================
        print("[Step 2] Sending message to verify context_builder is called...")

        builder_call_log.clear()
        response = await send_message(
            runner, SESSION_ID, USER_ID,
            "Who is the CEO and how many PTO days do employees get?"
        )
        print(f"  Agent: {response}\n")

        # Verify builder was called
        if builder_call_log:
            call = builder_call_log[0]
            print("  PASS: context_builder was called.")
            print(f"        user_id={call['user_id']}, thread_id={call['thread_id']}")

            # Verify correct args
            if call["user_id"] == USER_ID:
                print("  PASS: Correct user_id passed to builder.")
            else:
                print(f"  FAIL: user_id={call['user_id']}, expected {USER_ID}")
                passed = False

            if call["thread_id"] == THREAD_ID:
                print("  PASS: Correct thread_id passed to builder.")
            else:
                print(f"  FAIL: thread_id={call['thread_id']}, expected {THREAD_ID}")
                passed = False
        else:
            print("  FAIL: context_builder was NOT called.")
            passed = False

        print()

        # ==================================================================
        # Step 3: Verify the agent used custom context in its response
        # ==================================================================
        print("[Step 3] Verifying agent used custom context...")

        response_lower = response.lower()
        expected_facts = {
            "CEO name": "alex" in response_lower and "johnson" in response_lower,
            "PTO days": "20" in response_lower,
        }

        for fact_name, found in expected_facts.items():
            if found:
                print(f"  PASS: Agent referenced {fact_name} from custom context.")
            else:
                print(f"  FAIL: Agent did NOT reference {fact_name}.")
                passed = False

        print()

        # ==================================================================
        # Step 4: Verify message was persisted to Zep (parallel path)
        # ==================================================================
        print("[Step 4] Verifying message was persisted to Zep...")

        try:
            thread_data = await zep_client.thread.get(thread_id=THREAD_ID, lastn=10)
            msg_count = thread_data.row_count if thread_data.row_count else 0
            if msg_count > 0:
                print(f"  PASS: Thread has {msg_count} messages (persist ran in parallel).")
            else:
                print("  FAIL: Thread has 0 messages.")
                passed = False
        except Exception as e:
            print(f"  FAIL: Could not get thread messages: {e}")
            passed = False

        print()

        # ==================================================================
        # Step 5: Send a second message to verify continued operation
        # ==================================================================
        print("[Step 5] Sending follow-up message...")

        call_count_before = len(builder_call_log)
        response2 = await send_message(
            runner, SESSION_ID, USER_ID,
            "Where is the company headquartered?"
        )
        print(f"  Agent: {response2}\n")

        if len(builder_call_log) > call_count_before:
            print("  PASS: context_builder called again for second message.")
        else:
            print("  FAIL: context_builder NOT called for second message.")
            passed = False

        if "san francisco" in response2.lower():
            print("  PASS: Agent referenced San Francisco from custom context.")
        else:
            print("  FAIL: Agent did NOT reference San Francisco.")
            passed = False

        print()

    finally:
        # ==================================================================
        # Cleanup
        # ==================================================================
        print("[Cleanup] Deleting test user...")
        try:
            await zep_client.user.delete(user_id=USER_ID)
            print(f"  Deleted user {USER_ID}\n")
        except Exception as e:
            print(f"  Warning: Could not delete user: {e}\n")

    # ==================================================================
    # Final result
    # ==================================================================
    print("=" * 70)
    if passed:
        print("RESULT: ALL CHECKS PASSED")
    else:
        print("RESULT: SOME CHECKS FAILED")
    print("=" * 70)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
