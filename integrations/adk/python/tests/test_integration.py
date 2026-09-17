"""
End-to-end integration test for the Zep ADK integration.

Tests the full lifecycle against the Zep v4 API:
  1. Out-of-band provisioning with ``create_user`` and ``create_thread``,
     before the first turn.  Zep generates the user, graph, and thread UUIDs.
  2. A custom ontology set on the user graph with ``graph.set_ontology``.
  3. A user created with the correct metadata (first_name, last_name, email).
  4. Messages persisted to the correct thread.
  5. The agent responds coherently to user messages.
  6. Cross-thread memory recall (a new thread recalls facts from another one).
  7. ZepGraphSearchTool: the model invokes graph search when it is asked to.
  8. Zep resource verification through the SDK (user, threads, messages).
  9. ZepMemoryService.search_memory against the live client (the ADK-native
     memory extension point completes without raising).

Requires:
    ZEP_API_KEY and GOOGLE_API_KEY environment variables.

Usage:
    uv run python -m pytest tests/test_integration.py -v -s
    # or standalone:
    uv run python tests/test_integration.py
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
from zep_cloud import EntityProperty, EntityType  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_adk import (  # noqa: E402
    ZepContextTool,
    ZepGraphSearchTool,
    ZepMemoryService,
    create_after_model_callback,
    create_thread,
    create_user,
)

# Unique names per run to avoid collisions.  In v4 these are names, not
# addresses: every call below uses a server-generated UUID.
_suffix = uuid4().hex[:8]
USER_NAME_ID = f"adk-integ-{_suffix}"
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
# Custom ontology for the provisioning step
# ---------------------------------------------------------------------------
COMPANY_ENTITY_TYPE = EntityType(
    name="Company",
    description="A company or organization the user is associated with.",
    properties=[
        EntityProperty(
            name="industry",
            description="The company's industry",
            type="text",
        )
    ],
)


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
    graph_uuid: str,
    timeout_seconds: int = 120,
    poll_interval: float = 3.0,
) -> None:
    """Poll the episodes of a graph until Zep processes all of them."""
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout_seconds:
            logger.warning(
                "Timed out after %ds waiting for episodes to process. Continuing anyway.",
                timeout_seconds,
            )
            return

        try:
            pager = await zep_client.graph.episode.list(graph_uuid, limit=20)
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            await asyncio.sleep(poll_interval)
            continue
        episodes = pager.items or []

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


async def count_messages(zep_client: AsyncZep, thread_uuid: str) -> int:
    """Count the messages on one page of a thread."""
    pager = await zep_client.thread.list_messages(thread_uuid, limit=20)
    return len(pager.items or [])


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
    user_uuid = ""

    print(f"\n{'=' * 70}")
    print("Zep ADK Integration Test (v4)")
    print(f"{'=' * 70}\n")

    try:
        # ==================================================================
        # Step 1: Out-of-band provisioning with create_user / create_thread,
        # BEFORE the agent ever runs.  Zep generates every UUID.
        # ==================================================================
        print("[Step 1] Provisioning the Zep user and threads out-of-band...")

        user = await create_user(
            zep_client,
            user_id=USER_NAME_ID,
            first_name=FIRST_NAME,
            last_name=LAST_NAME,
            email=EMAIL,
        )
        user_uuid = user.uuid_
        graph_uuid = user.graph_uuid

        passed &= check("create_user returns a user UUID", bool(user_uuid), f"uuid={user_uuid}")
        passed &= check("The user has a graph UUID", bool(graph_uuid), f"graph={graph_uuid}")

        # A custom ontology on the user graph. This is one-time per-user
        # setup that an application does at provisioning time.
        await zep_client.graph.set_ontology(graph_uuid, entity_types=[COMPANY_ENTITY_TYPE])

        # Threads 2-4 are used later in the test (cross-thread recall, graph
        # search, tool-call persistence). Provision them all up-front, since
        # the turn path itself never creates a thread.
        thread_1 = await create_thread(zep_client, user_uuid=user_uuid)
        thread_2 = await create_thread(zep_client, user_uuid=user_uuid)
        thread_3 = await create_thread(zep_client, user_uuid=user_uuid)
        thread_4 = await create_thread(zep_client, user_uuid=user_uuid)

        passed &= check(
            "create_thread returns a thread UUID",
            bool(thread_1.uuid_),
            f"uuid={thread_1.uuid_}",
        )
        passed &= check(
            "The thread belongs to the user",
            thread_1.user_uuid == user_uuid,
            f"user_uuid={thread_1.user_uuid}",
        )

        # ==================================================================
        # Step 2: Create the agent. The turn path never creates Zep resources
        # -- provisioning already happened out-of-band above.
        # ==================================================================
        print("\n[Step 2] Creating agent (no provisioning on the turn path)...")

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

        base_state = {
            "zep_user_uuid": user_uuid,
            "zep_graph_uuid": graph_uuid,
            "zep_first_name": FIRST_NAME,
            "zep_last_name": LAST_NAME,
        }

        # ==================================================================
        # Step 3: Session 1 — seed facts on the already-provisioned thread
        # ==================================================================
        print("[Step 3] Session 1: seeding facts on the pre-provisioned thread...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_uuid,
            session_id=thread_1.uuid_,
            state={**base_state, "zep_thread_uuid": thread_1.uuid_},
        )

        seed_message = (
            "My name is IntegTest and I work at Acme Corp as a data scientist. "
            "I love hiking and photography. I live in Portland, Oregon."
        )
        print(f"  User:  {seed_message}")
        response1 = await send_message(runner, thread_1.uuid_, user_uuid, seed_message)
        print(f"  Agent: {response1}\n")

        passed &= check("Agent returned a non-empty response", len(response1) > 0)

        # ==================================================================
        # Step 4: Verify the custom ontology on the user graph
        # ==================================================================
        print("\n[Step 4] Verifying the custom ontology on the user graph...")

        ontology = await zep_client.graph.get_ontology(graph_uuid)
        entity_names = [et.name for et in (ontology.entity_types or [])]
        print(f"  Entity types found: {entity_names}")

        passed &= check(
            "Custom 'Company' entity type exists in the user ontology",
            "Company" in entity_names,
            f"entity_types={entity_names}",
        )

        company_type = next(
            (et for et in (ontology.entity_types or []) if et.name == "Company"), None
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
        # Step 5: Verify Zep user metadata
        # ==================================================================
        print("\n[Step 5] Verifying Zep user metadata via SDK...")

        try:
            fetched = await zep_client.user.get(user_uuid)
            print(f"  User found: {fetched.uuid_}")
            passed &= check(
                "first_name matches", fetched.first_name == FIRST_NAME, f"{fetched.first_name}"
            )
            passed &= check(
                "last_name matches", fetched.last_name == LAST_NAME, f"{fetched.last_name}"
            )
            passed &= check("email matches", fetched.email == EMAIL, f"{fetched.email}")
        except Exception as e:
            print(f"  FAIL: Could not get user: {e}")
            passed = False

        # ==================================================================
        # Step 6: Verify thread 1 has messages
        # ==================================================================
        print("\n[Step 6] Verifying thread 1 has messages...")

        try:
            msg_count = await count_messages(zep_client, thread_1.uuid_)
            print(f"  Thread 1 message count: {msg_count}")
            passed &= check("Thread 1 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not list thread 1 messages: {e}")
            passed = False

        # ==================================================================
        # Step 7: Wait for Zep to process episodes, then wait a second time
        # so that graph node/edge extraction (which runs after episode
        # processing) has time to complete before recall and search.
        # ==================================================================
        print("\n[Step 7] Waiting for Zep to process episodes (pass 1)...")
        await wait_for_episodes_processed(zep_client, graph_uuid, timeout_seconds=180)
        print("\n[Step 7b] Waiting for graph node/edge extraction (pass 2)...")
        await wait_for_episodes_processed(zep_client, graph_uuid, timeout_seconds=180)

        # ==================================================================
        # Step 8: Session 2 — cross-thread memory recall
        # ==================================================================
        print("\n[Step 8] Session 2: testing cross-thread memory recall...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_uuid,
            session_id=thread_2.uuid_,
            state={**base_state, "zep_thread_uuid": thread_2.uuid_},
        )

        recall_message = "What do you know about me?"
        print(f"  User:  {recall_message}")
        response2 = await send_message(runner, thread_2.uuid_, user_uuid, recall_message)
        print(f"  Agent: {response2}\n")

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
        # Step 9: Verify thread 2 also has messages
        # ==================================================================
        print("\n[Step 9] Verifying thread 2 has messages...")

        try:
            msg_count = await count_messages(zep_client, thread_2.uuid_)
            print(f"  Thread 2 message count: {msg_count}")
            passed &= check("Thread 2 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not list thread 2 messages: {e}")
            passed = False

        # ==================================================================
        # Step 9b: ZepMemoryService.search_memory against the live client.
        # Content assertions are intentionally lenient -- graph ingestion is
        # asynchronous, so the search may or may not have picked up the
        # seeded facts yet. This step only asserts the ADK-native memory
        # service extension point round-trips against live Zep without
        # raising and returns a well-formed SearchMemoryResponse.
        # ==================================================================
        print("\n[Step 9b] ZepMemoryService.search_memory against the live client...")

        memory_service = ZepMemoryService(zep=zep_client)
        try:
            memory_response = await memory_service.search_memory(
                app_name=APP_NAME,
                user_id=user_uuid,
                query="What do you know about the user's hobbies?",
            )
            print(f"  ZepMemoryService returned {len(memory_response.memories)} memories")
            passed &= check(
                "ZepMemoryService.search_memory returned a SearchMemoryResponse",
                memory_response is not None,
            )
        except Exception as e:
            print(f"  FAIL: ZepMemoryService.search_memory raised: {e}")
            passed = False

        # ==================================================================
        # Step 10: Session 3 — ZepGraphSearchTool (model-initiated search)
        # ==================================================================
        print("\n[Step 10] Session 3: testing ZepGraphSearchTool (model calls search tool)...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_uuid,
            session_id=thread_3.uuid_,
            state={**base_state, "zep_thread_uuid": thread_3.uuid_},
        )

        search_message = (
            "Use the search_user_memory tool to search for what you know "
            "about my hobbies. Tell me exactly what the search returns."
        )
        print(f"  User:  {search_message}")
        response3 = await send_message(runner, thread_3.uuid_, user_uuid, search_message)
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
        # Step 11: Verify thread 3 also has messages
        # ==================================================================
        print("\n[Step 11] Verifying thread 3 has messages...")

        try:
            msg_count = await count_messages(zep_client, thread_3.uuid_)
            print(f"  Thread 3 message count: {msg_count}")
            passed &= check("Thread 3 has messages", msg_count > 0, f"count={msg_count}")
        except Exception as e:
            print(f"  FAIL: Could not list thread 3 messages: {e}")
            passed = False

        # ==================================================================
        # Step 12: Session 5 — ZepGraphSearchTool with scope="auto"
        # ==================================================================
        print("\n[Step 12] Session 5: testing ZepGraphSearchTool with scope='auto'...")

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

        thread_5 = await create_thread(zep_client, user_uuid=user_uuid)
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_uuid,
            session_id=thread_5.uuid_,
            state={"zep_user_uuid": user_uuid, "zep_graph_uuid": graph_uuid},
        )

        auto_search_message = (
            "Use the search_user_memory tool to search for what you know "
            "about where I live. Tell me exactly what the search returns."
        )
        print(f"  User:  {auto_search_message}")
        response5 = await send_message(auto_runner, thread_5.uuid_, user_uuid, auto_search_message)
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
        # Step 13: Tool-call message persistence — verify only final
        # assistant message is persisted (not intermediate "thoughts")
        # ==================================================================
        print("\n[Step 13] Session 4: tool-call persistence test (get_current_weather)...")

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_uuid,
            session_id=thread_4.uuid_,
            state={**base_state, "zep_thread_uuid": thread_4.uuid_},
        )

        tool_message = "What's the current weather in Portland? Use the get_current_weather tool."
        print(f"  User:  {tool_message}")
        response4 = await send_message(runner, thread_4.uuid_, user_uuid, tool_message)
        print(f"  Agent: {response4}\n")

        passed &= check("Agent responded after tool call", len(response4) > 0)

        # Give Zep a moment to process the messages
        await asyncio.sleep(2)

        # Fetch all messages from thread 4 and inspect
        print("  Inspecting messages persisted to Zep thread 4:")
        try:
            pager = await zep_client.thread.list_messages(thread_4.uuid_, limit=20)
            messages = pager.items or []
            for i, msg in enumerate(messages):
                role = msg.role or "unknown"
                content_preview = (msg.content or "")[:120]
                print(f"    [{i}] role={role}: {content_preview}")

            # Count by role
            user_msgs = [m for m in messages if m.role == "user"]
            asst_msgs = [m for m in messages if m.role == "assistant"]
            tool_msgs = [m for m in messages if m.role in ("tool", "function")]

            print(
                f"\n  Summary: {len(user_msgs)} user, "
                f"{len(asst_msgs)} assistant, {len(tool_msgs)} tool"
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
            print(f"  FAIL: Could not list thread 4 messages: {e}")
            passed = False

    finally:
        # ==================================================================
        # Cleanup.  A v4 delete returns an asynchronous task; the resource
        # disappears a short time later.
        # ==================================================================
        print("\n[Cleanup] Deleting test user (cascades to threads)...")
        if user_uuid:
            try:
                await zep_client.user.delete(user_uuid)
                print(f"  Requested deletion of user {user_uuid}\n")
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

    assert passed, "One or more integration checks failed — see PASS/FAIL lines above"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live ADK integration test."""
    await main()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError:
        sys.exit(1)
