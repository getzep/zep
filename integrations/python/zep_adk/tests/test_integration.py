"""
End-to-end integration test for the Zep ADK integration.

Tests the full lifecycle:
  1. Custom ontology via on_user_created hook (set_ontology + verify via list_entity_types)
  2. Lazy user creation with correct metadata (first_name, last_name, email)
  3. Lazy thread creation with messages persisted to the correct thread
  4. Agent responds coherently to user messages
  5. Cross-thread memory recall (new session recalls facts from a different thread)
  6. on_user_created hook fires exactly once (not on existing users)
  7. ZepGraphSearchTool: model invokes graph search when asked
  8. Zep resource verification via SDK (user, threads, messages)

Requires:
    ZEP_API_KEY and GOOGLE_API_KEY environment variables.

Usage:
    source /Users/jackryan/.env.zep_production && uv run python -m pytest tests/test_integration.py -v -s
    # or standalone:
    source /Users/jackryan/.env.zep_production && uv run python tests/test_integration.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Configuration — skip entire module when API keys are not available
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

if not ZEP_API_KEY or not GOOGLE_API_KEY:
    pytest.skip(
        "ZEP_API_KEY and GOOGLE_API_KEY required for integration tests",
        allow_module_level=True,
    )

from google.adk.agents import Agent  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import Field  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402
from zep_cloud.external_clients.ontology import EntityModel, EntityText  # noqa: E402

from zep_adk import ZepContextTool, ZepGraphSearchTool, create_after_model_callback  # noqa: E402

# Unique IDs per run to avoid collisions
_suffix = uuid4().hex[:8]
USER_ID = f"adk-integ-{_suffix}"
SESSION_1_ID = f"adk-integ-s1-{_suffix}"
SESSION_2_ID = f"adk-integ-s2-{_suffix}"
SESSION_3_ID = f"adk-integ-s3-{_suffix}"
SESSION_4_ID = f"adk-integ-s4-{_suffix}"
SESSION_5_ID = f"adk-integ-s5-{_suffix}"
THREAD_1_ID = f"adk-integ-t1-{_suffix}"
THREAD_2_ID = f"adk-integ-t2-{_suffix}"
THREAD_3_ID = f"adk-integ-t3-{_suffix}"
THREAD_4_ID = f"adk-integ-t4-{_suffix}"
APP_NAME = "zep-adk-integ-test"

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_integration")

# Suppress noisy library logs
logging.getLogger("google.adk").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Custom ontology for testing on_user_created hook
# ---------------------------------------------------------------------------
class Company(EntityModel):
    """A company or organization the user is associated with."""

    industry: EntityText = Field(description="The company's industry", default=None)


# ---------------------------------------------------------------------------
# Simple tool for testing tool-call message persistence
# ---------------------------------------------------------------------------
def get_current_weather(city: str) -> dict:
    """Get the current weather for a city. This is a fake tool for testing."""
    return {"city": city, "temperature": "72°F", "condition": "sunny"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


async def wait_for_episodes_processed(
    zep_client: AsyncZep,
    user_id: str,
    timeout_seconds: int = 120,
    poll_interval: float = 3.0,
) -> None:
    """Poll Zep episodes until all are processed or timeout is reached."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout_seconds:
            logger.warning(
                "Timed out after %ds waiting for episodes to process. Continuing anyway.",
                timeout_seconds,
            )
            return

        episodes_resp = await zep_client.graph.episode.get_by_user_id(user_id=user_id, lastn=10)
        episodes = episodes_resp.episodes or []

        if not episodes:
            logger.info("No episodes found yet, waiting...")
            await asyncio.sleep(poll_interval)
            continue

        unprocessed = [e for e in episodes if not e.processed]
        if not unprocessed:
            logger.info("All %d episodes are processed.", len(episodes))
            return

        logger.info(
            "Waiting for episodes: %d/%d processed (%.0fs elapsed)",
            len(episodes) - len(unprocessed),
            len(episodes),
            elapsed,
        )
        await asyncio.sleep(poll_interval)


def check(description: str, condition: bool, detail: str = "") -> bool:
    """Print a PASS/FAIL line and return the condition."""
    status = "PASS" if condition else "FAIL"
    msg = f"  {status}: {description}"
    if detail:
        msg += f" ({detail})"
    print(msg)
    return condition


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------
async def main() -> None:
    zep_client = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep ADK Integration Test")
    print(f"{'=' * 70}")
    print(f"  User ID:  {USER_ID}")
    print(f"  Threads:  {THREAD_1_ID}, {THREAD_2_ID}, {THREAD_3_ID}, {THREAD_4_ID}")
    print(f"{'=' * 70}\n")

    try:
        # ==================================================================
        # Step 1: Create the agent with on_user_created hook that sets ontology
        # ==================================================================
        print("[Step 1] Creating agent with on_user_created hook (sets custom ontology)...")

        hook_calls: list[str] = []

        async def on_user_created(zep: AsyncZep, user_id: str) -> None:
            """Set a custom ontology when a new Zep user is created."""
            hook_calls.append(user_id)
            logger.info("on_user_created hook fired for %s — setting ontology", user_id)
            await zep.graph.set_ontology(
                entities={"Company": Company},
                user_ids=[user_id],
            )

        agent = Agent(
            name="zep_integ_test_agent",
            model="gemini-2.5-flash",
            description="A test agent with Zep long-term memory.",
            instruction=(
                "You are a helpful assistant with access to long-term memory. "
                "When context from Zep is injected into the prompt, use it to "
                "provide personalised, memory-aware responses. "
                "If you know something about the user from memory, reference it naturally. "
                "Be concise in your responses."
            ),
            tools=[
                ZepContextTool(
                    zep_client=zep_client,
                    ignore_roles=["assistant"],
                    on_user_created=on_user_created,
                ),
                ZepGraphSearchTool(
                    zep_client=zep_client,
                    name="search_user_memory",
                    description=(
                        "Search the user's knowledge graph for information from "
                        "previous conversations, known facts, or general context "
                        "about the user. Use this to look up specific details the "
                        "user has shared before."
                    ),
                ),
                get_current_weather,
            ],
            after_model_callback=create_after_model_callback(
                zep_client=zep_client,
                ignore_roles=["assistant"],
            ),
        )

        session_service = InMemorySessionService()
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
        print("  Agent created.\n")

        # ==================================================================
        # Step 2: Session 1 — seed facts and trigger user creation
        # ==================================================================
        print("[Step 2] Session 1: seeding facts (triggers lazy user + thread creation)...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_1_ID,
            state={
                "zep_thread_id": THREAD_1_ID,
                "zep_first_name": FIRST_NAME,
                "zep_last_name": LAST_NAME,
                "zep_email": EMAIL,
            },
        )

        seed_message = (
            "My name is IntegTest and I work at Acme Corp as a data scientist. "
            "I love hiking and photography. I live in Portland, Oregon."
        )
        print(f"  User:  {seed_message}")
        response1 = await send_message(runner, SESSION_1_ID, USER_ID, seed_message)
        print(f"  Agent: {response1}\n")

        passed &= check("Agent returned a non-empty response", len(response1) > 0)
        passed &= check(
            "on_user_created hook fired exactly once",
            len(hook_calls) == 1 and hook_calls[0] == USER_ID,
            f"calls={hook_calls}",
        )

        # ==================================================================
        # Step 3: Verify custom ontology was set by the hook
        # ==================================================================
        print("\n[Step 3] Verifying custom ontology set by on_user_created hook...")

        ontology_resp = await zep_client.graph.list_entity_types(user_id=USER_ID)
        entity_names = [et.name for et in (ontology_resp.entity_types or [])]
        print(f"  Entity types found: {entity_names}")

        passed &= check(
            "Custom 'Company' entity type exists in user ontology",
            "Company" in entity_names,
            f"entity_types={entity_names}",
        )

        # Verify the Company entity has the expected property
        company_type = next(
            (et for et in (ontology_resp.entity_types or []) if et.name == "Company"), None
        )
        if company_type and company_type.properties:
            prop_names = [p.name for p in company_type.properties]
            print(f"  Company properties: {prop_names}")
            passed &= check(
                "Company entity has 'industry' property",
                "industry" in prop_names,
                f"properties={prop_names}",
            )
        else:
            print("  FAIL: Company entity type not found or has no properties")
            passed = False

        # ==================================================================
        # Step 4: Verify Zep user was created with correct metadata
        # ==================================================================
        print("\n[Step 4] Verifying Zep user metadata via SDK...")

        try:
            user = await zep_client.user.get(user_id=USER_ID)
            print(f"  User found: {user.user_id}")
            passed &= check(
                "first_name matches", user.first_name == FIRST_NAME, f"{user.first_name}"
            )
            passed &= check("last_name matches", user.last_name == LAST_NAME, f"{user.last_name}")
            passed &= check("email matches", user.email == EMAIL, f"{user.email}")
        except Exception as e:
            print(f"  FAIL: Could not get user: {e}")
            passed = False

        # ==================================================================
        # Step 5: Verify thread 1 exists with messages
        # ==================================================================
        print("\n[Step 5] Verifying thread 1 has messages...")

        try:
            t1 = await zep_client.thread.get(thread_id=THREAD_1_ID, lastn=10)
            msg_count = t1.row_count if t1.row_count else 0
            print(f"  Thread 1 message count: {msg_count}")
            passed &= check("Thread 1 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not get thread 1: {e}")
            passed = False

        # ==================================================================
        # Step 6: Wait for Zep to process episodes
        # ==================================================================
        print("\n[Step 6] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep_client, USER_ID, timeout_seconds=120)

        # ==================================================================
        # Step 7: Session 2 — cross-thread memory recall
        # ==================================================================
        print("\n[Step 7] Session 2: testing cross-thread memory recall...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_2_ID,
            state={
                "zep_thread_id": THREAD_2_ID,
                "zep_first_name": FIRST_NAME,
                "zep_last_name": LAST_NAME,
                "zep_email": EMAIL,
            },
        )

        recall_message = "What do you know about me?"
        print(f"  User:  {recall_message}")
        response2 = await send_message(runner, SESSION_2_ID, USER_ID, recall_message)
        print(f"  Agent: {response2}\n")

        passed &= check(
            "on_user_created hook did NOT fire again for existing user",
            len(hook_calls) == 1,
            f"hook_calls={len(hook_calls)}",
        )

        # Check that the agent recalled at least some seeded facts
        recall_keywords = ["acme", "data scientist", "hiking", "photography", "portland"]
        response_lower = response2.lower()
        found_keywords = [kw for kw in recall_keywords if kw in response_lower]
        print(f"  Recall keywords found: {found_keywords}")

        passed &= check(
            "Agent recalled facts from first conversation",
            len(found_keywords) > 0,
            f"found={found_keywords}, expected_one_of={recall_keywords}",
        )

        # ==================================================================
        # Step 8: Verify thread 2 also has messages
        # ==================================================================
        print("\n[Step 8] Verifying thread 2 has messages...")

        try:
            t2 = await zep_client.thread.get(thread_id=THREAD_2_ID, lastn=10)
            msg_count = t2.row_count if t2.row_count else 0
            print(f"  Thread 2 message count: {msg_count}")
            passed &= check("Thread 2 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not get thread 2: {e}")
            passed = False

        # ==================================================================
        # Step 9: Session 3 — ZepGraphSearchTool (model-initiated search)
        # ==================================================================
        print("\n[Step 9] Session 3: testing ZepGraphSearchTool (model calls search tool)...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_3_ID,
            state={
                "zep_thread_id": THREAD_3_ID,
                "zep_first_name": FIRST_NAME,
                "zep_last_name": LAST_NAME,
                "zep_email": EMAIL,
            },
        )

        search_message = (
            "Use the search_user_memory tool to search for what you know "
            "about my hobbies. Tell me exactly what the search returns."
        )
        print(f"  User:  {search_message}")
        response3 = await send_message(runner, SESSION_3_ID, USER_ID, search_message)
        print(f"  Agent: {response3}\n")

        response3_lower = response3.lower()
        hobby_keywords = ["hiking", "photography"]
        found_hobby_kw = [kw for kw in hobby_keywords if kw in response3_lower]
        print(f"  Hobby keywords found: {found_hobby_kw}")

        passed &= check(
            "Graph search tool returned hobby facts",
            len(found_hobby_kw) > 0,
            f"found={found_hobby_kw}, expected_one_of={hobby_keywords}",
        )

        # ==================================================================
        # Step 10: Verify thread 3 also has messages
        # ==================================================================
        print("\n[Step 10] Verifying thread 3 has messages...")

        try:
            t3 = await zep_client.thread.get(thread_id=THREAD_3_ID, lastn=10)
            msg_count = t3.row_count if t3.row_count else 0
            print(f"  Thread 3 message count: {msg_count}")
            passed &= check("Thread 3 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not get thread 3: {e}")
            passed = False

        # ==================================================================
        # Step 11: Session 5 — ZepGraphSearchTool with scope="auto"
        # ==================================================================
        print("\n[Step 11] Session 5: testing ZepGraphSearchTool with scope='auto'...")

        # Build a separate agent with ONLY the graph search tool (no
        # ZepContextTool) so the model has no pre-injected memory and must
        # call the search tool to answer questions about the user.
        auto_agent = Agent(
            name="zep_integ_auto_scope_agent",
            model="gemini-2.5-flash",
            description="A test agent with auto-scope graph search.",
            instruction=(
                "You have no prior knowledge about the user. You MUST use "
                "the search_user_memory tool to answer any question about them."
            ),
            tools=[
                ZepGraphSearchTool(
                    zep_client=zep_client,
                    name="search_user_memory",
                    description="Search the user's knowledge graph.",
                    scope="auto",
                ),
            ],
        )

        auto_runner = Runner(
            agent=auto_agent,
            app_name=APP_NAME,
            session_service=session_service,
        )

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_5_ID,
            state={"zep_user_id": USER_ID},
        )

        auto_search_message = (
            "Use the search_user_memory tool to search for what you know "
            "about where I live. Tell me exactly what the search returns."
        )
        print(f"  User:  {auto_search_message}")
        response5 = await send_message(auto_runner, SESSION_5_ID, USER_ID, auto_search_message)
        print(f"  Agent: {response5}\n")

        response5_lower = response5.lower()
        auto_keywords = ["portland", "oregon"]
        found_auto_kw = [kw for kw in auto_keywords if kw in response5_lower]
        print(f"  Auto-scope keywords found: {found_auto_kw}")

        passed &= check(
            "Auto-scope graph search returned location facts",
            len(found_auto_kw) > 0,
            f"found={found_auto_kw}, expected_one_of={auto_keywords}",
        )

        # ==================================================================
        # Step 12: Tool-call message persistence — verify only final
        # assistant message is persisted (not intermediate "thoughts")
        # ==================================================================
        print("\n[Step 12] Session 4: tool-call persistence test (get_current_weather)...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_4_ID,
            state={
                "zep_thread_id": THREAD_4_ID,
                "zep_first_name": FIRST_NAME,
                "zep_last_name": LAST_NAME,
                "zep_email": EMAIL,
            },
        )

        tool_message = "What's the current weather in Portland? Use the get_current_weather tool."
        print(f"  User:  {tool_message}")
        response4 = await send_message(runner, SESSION_4_ID, USER_ID, tool_message)
        print(f"  Agent: {response4}\n")

        passed &= check("Agent responded after tool call", len(response4) > 0)

        # Give Zep a moment to process the messages
        await asyncio.sleep(2)

        # Fetch all messages from thread 4 and inspect
        print("  Inspecting messages persisted to Zep thread 4:")
        try:
            t4_resp = await zep_client.thread.get(thread_id=THREAD_4_ID, lastn=20)
            messages = t4_resp.messages or []
            for i, msg in enumerate(messages):
                role = msg.role or "unknown"
                content_preview = (msg.content or "")[:120]
                print(f"    [{i}] role={role}: {content_preview}")

            # Count by role
            user_msgs = [m for m in messages if m.role == "user"]
            asst_msgs = [m for m in messages if m.role == "assistant"]
            tool_msgs = [m for m in messages if m.role in ("tool", "function")]

            print(
                f"\n  Summary: {len(user_msgs)} user, {len(asst_msgs)} assistant, {len(tool_msgs)} tool"
            )

            passed &= check(
                "Exactly 1 user message persisted",
                len(user_msgs) == 1,
                f"got {len(user_msgs)}",
            )
            passed &= check(
                "Exactly 1 assistant message persisted (no intermediate thoughts)",
                len(asst_msgs) == 1,
                f"got {len(asst_msgs)}",
            )
            passed &= check(
                "No tool/function messages persisted",
                len(tool_msgs) == 0,
                f"got {len(tool_msgs)}",
            )

            # The single assistant message should contain weather info
            if asst_msgs:
                asst_text = (asst_msgs[0].content or "").lower()
                passed &= check(
                    "Assistant message contains weather result (not just 'let me check')",
                    "72" in asst_text or "sunny" in asst_text or "portland" in asst_text,
                    f"text={asst_msgs[0].content[:100]}",
                )

        except Exception as e:
            print(f"  FAIL: Could not get thread 4 messages: {e}")
            passed = False

    finally:
        # ==================================================================
        # Cleanup
        # ==================================================================
        print("\n[Cleanup] Deleting test user (cascades to threads)...")
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
