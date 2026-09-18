"""
End-to-end integration test for the Zep AG2 integration.

Exercises the full lifecycle against live Zep and OpenAI:

  1. ``ZepMemoryManager.add_messages`` persists a conversation to a Zep thread.
  2. Both sides of the conversation are captured on the thread.
  3. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
  4. A live AG2 agent, enriched with Zep context, recalls those facts in its reply.
  5. Zep resource verification via the SDK (user metadata, thread messages).

Zep v4 addresses every user, thread, and graph by a server-generated UUID. The
test creates the user and the threads one time and keeps the UUIDs from the
responses.

ZEPAI-3605: a user that has no user_id cannot receive a thread message until
the fix is deployed. The test is skipped until then.

Requires:
    ZEP_API_KEY and OPENAI_API_KEY environment variables.

Usage:
    uv run pytest tests/test_integration.py -v -s -m integration
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
# Configuration -- skip the whole module when API keys are not available.
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ZEP_BASE_URL = os.environ.get("ZEP_BASE_URL", "https://api.getzep.com/api/v4")

if not ZEP_API_KEY or not OPENAI_API_KEY:
    pytest.skip(
        "ZEP_API_KEY and OPENAI_API_KEY required for integration tests",
        allow_module_level=True,
    )

from autogen import AssistantAgent, LLMConfig, UserProxyAgent  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools  # noqa: E402

FIRST_NAME = "IntegTest"
LAST_NAME = "User"

# The v4 server cannot add a message to a thread whose user has no ``user_id``,
# so the live test gives the user a unique label. The package API stays
# UUID-only.
USER_LABEL = f"integtest-{uuid4().hex[:8]}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_integration")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

KEYWORDS = ["acme", "data scientist", "portland", "hiking", "photography"]

SEED_MESSAGES = [
    {
        "role": "user",
        "name": FIRST_NAME,
        "content": "My name is IntegTest. I work at Acme Corp as a data scientist.",
    },
    {"role": "assistant", "content": "Noted -- a data scientist at Acme Corp."},
    {
        "role": "user",
        "name": FIRST_NAME,
        "content": "I live in Portland, Oregon and I love hiking and photography.",
    },
    {"role": "assistant", "content": "Got it -- Portland, Oregon, hiking and photography."},
]


def make_client() -> AsyncZep:
    """Create the v4 client with an explicit v4 base URL."""
    return AsyncZep(api_key=ZEP_API_KEY, base_url=ZEP_BASE_URL)


def check(description: str, condition: bool, detail: str = "") -> bool:
    """Print a PASS/FAIL line and return the condition."""
    status = "PASS" if condition else "FAIL"
    msg = f"  {status}: {description}"
    if detail:
        msg += f" ({detail})"
    print(msg)
    return condition


async def wait_for_episodes_processed(
    zep: AsyncZep,
    graph_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: float = 3.0,
) -> None:
    """Poll Zep episodes until all are processed or the timeout is reached."""
    start = time.monotonic()
    while True:
        if time.monotonic() - start > timeout_seconds:
            logger.warning("Timed out waiting for episode processing; continuing.")
            return
        try:
            pager = await zep.graph.episode.list(graph_uuid, limit=20)
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            await asyncio.sleep(poll_interval)
            continue
        episodes = pager.items or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent(
    zep: AsyncZep, graph_uuid: str, thread_uuid: str
) -> tuple[AssistantAgent, UserProxyAgent]:
    """Build an AG2 assistant/user pair wired to Zep memory on the given thread."""
    llm_config = LLMConfig({"model": OPENAI_MODEL, "api_key": OPENAI_API_KEY})
    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message=(
            "You are a helpful assistant with long-term memory. When context from "
            "Zep is provided, use it to answer questions about the user. Be concise."
        ),
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )
    register_all_tools(assistant, user_proxy, zep, graph_uuid, thread_uuid)
    return assistant, user_proxy


async def main() -> None:
    zep = make_client()
    passed = True
    user_uuid = ""

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        user = await create_user(
            zep, user_id=USER_LABEL, first_name=FIRST_NAME, last_name=LAST_NAME
        )
        user_uuid = user.uuid_ or ""
        graph_uuid = user.graph_uuid or ""
        thread_1 = await create_thread(zep, user_uuid=user_uuid)
        thread_1_uuid = thread_1.uuid_ or ""

        print(f"\n{'=' * 70}")
        print("Zep AG2 Integration Test")
        print(f"  User UUID:   {user_uuid}")
        print(f"  Graph UUID:  {graph_uuid}")
        print(f"  Thread UUID: {thread_1_uuid}")
        print(f"{'=' * 70}\n")

        # -- Conversation 1: seed facts via the integration. -----------------
        print("[Step 1] Conversation 1: seeding facts...")
        manager1 = ZepMemoryManager(zep, user_uuid, thread_1_uuid, graph_uuid=graph_uuid)
        await manager1.add_messages(SEED_MESSAGES)
        for message in SEED_MESSAGES:
            print(f"  {message['role'].capitalize()}: {message['content']}")

        # -- Verify user metadata --------------------------------------------
        print("\n[Step 2] Verifying Zep user metadata...")
        stored_user = await zep.user.get(user_uuid)
        passed &= check(
            "first_name matches", stored_user.first_name == FIRST_NAME, str(stored_user.first_name)
        )
        passed &= check(
            "last_name matches", stored_user.last_name == LAST_NAME, str(stored_user.last_name)
        )

        # -- Verify thread 1 captured both sides -----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        pager = await zep.thread.list_messages(thread_1_uuid, limit=20)
        messages = pager.items or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ----------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=300)

        # -- Conversation 2: cross-thread memory recall ----------------------
        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        thread_2 = await create_thread(zep, user_uuid=user_uuid)
        thread_2_uuid = thread_2.uuid_ or ""
        manager2 = ZepMemoryManager(zep, user_uuid, thread_2_uuid, graph_uuid=graph_uuid)
        context = await manager2.get_memory_context(
            query="What do we know about IntegTest's job, location, and hobbies?"
        )
        recalled = context.lower()
        found = [kw for kw in KEYWORDS if kw in recalled]
        print(f"  Recalled keywords (context block): {found}")
        passed &= check(
            "Zep context recalls facts from conversation 1",
            len(found) > 0,
            f"found={found}",
        )

        assistant, user_proxy = build_agent(zep, graph_uuid, thread_2_uuid)
        await manager2.enrich_system_message(assistant, query="IntegTest profile and hobbies")
        result = user_proxy.initiate_chat(
            assistant,
            message=(
                "Based on what you know about me, where do I work, what is my role, and "
                "where do I live? Answer in one sentence, then say TERMINATE."
            ),
            max_turns=2,
        )
        transcript = " ".join(
            str(m.get("content") or "") for m in (result.chat_history or [])
        ).lower()
        agent_found = [kw for kw in KEYWORDS if kw in transcript]
        print(f"  Agent recalled keywords: {agent_found}")
        passed &= check(
            "Agent recalled facts from conversation 1",
            len(agent_found) > 0,
            f"found={agent_found}",
        )

    finally:
        print("\n[Cleanup] Deleting test user...")
        try:
            if user_uuid:
                await zep.user.delete(user_uuid)
                print(f"  Deleted {user_uuid}")
        except Exception as exc:
            print(f"  Warning: could not delete user: {exc}")

    print(f"\n{'=' * 70}")
    print("RESULT:", "ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED")
    print("=" * 70)
    sys.exit(0 if passed else 1)


@pytest.mark.skip(
    reason=(
        "ZEPAI-3605: a user that has no user_id cannot receive a thread message "
        "until the fix is deployed."
    )
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live integration test."""
    zep = make_client()
    user_uuid = ""

    try:
        user = await create_user(
            zep, user_id=USER_LABEL, first_name=FIRST_NAME, last_name=LAST_NAME
        )
        user_uuid = user.uuid_ or ""
        graph_uuid = user.graph_uuid or ""
        thread_1 = await create_thread(zep, user_uuid=user_uuid)
        thread_1_uuid = thread_1.uuid_ or ""

        manager1 = ZepMemoryManager(zep, user_uuid, thread_1_uuid, graph_uuid=graph_uuid)
        await manager1.add_messages(SEED_MESSAGES)

        stored_user = await zep.user.get(user_uuid)
        assert stored_user.first_name == FIRST_NAME
        assert stored_user.last_name == LAST_NAME

        pager = await zep.thread.list_messages(thread_1_uuid, limit=20)
        messages = pager.items or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=300)

        thread_2 = await create_thread(zep, user_uuid=user_uuid)
        thread_2_uuid = thread_2.uuid_ or ""
        manager2 = ZepMemoryManager(zep, user_uuid, thread_2_uuid, graph_uuid=graph_uuid)
        context = await manager2.get_memory_context(
            query="What do we know about IntegTest's job, location, and hobbies?"
        )
        assert any(kw in context.lower() for kw in KEYWORDS), f"no recall in context: {context}"

        assistant, user_proxy = build_agent(zep, graph_uuid, thread_2_uuid)
        await manager2.enrich_system_message(assistant, query="IntegTest profile and hobbies")
        result = user_proxy.initiate_chat(
            assistant,
            message=(
                "Based on what you know about me, where do I work, what is my role, and "
                "where do I live? Answer in one sentence, then say TERMINATE."
            ),
            max_turns=2,
        )
        transcript = " ".join(
            str(m.get("content") or "") for m in (result.chat_history or [])
        ).lower()
        assert any(kw in transcript for kw in KEYWORDS), f"no recall in: {transcript}"
    finally:
        try:
            if user_uuid:
                await zep.user.delete(user_uuid)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
