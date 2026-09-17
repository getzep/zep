"""
End-to-end integration test for the Zep AutoGen integration.

Exercises the full lifecycle against live Zep and OpenAI:

  1. ``ZepUserMemory.add`` persists user and assistant turns to a Zep thread.
  2. Both sides of the conversation are captured on the thread.
  3. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
  4. A live AutoGen agent (``memory=[ZepUserMemory]``) recalls those facts in its
     reply, with context injected automatically via ``update_context``.
  5. Zep resource verification via the SDK (user metadata, thread messages).

Zep v4 assigns the UUID of every user and thread, so the test creates the
resources and keeps the UUIDs that Zep returns.

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

if not ZEP_API_KEY or not OPENAI_API_KEY:
    pytest.skip(
        "ZEP_API_KEY and OPENAI_API_KEY required for integration tests",
        allow_module_level=True,
    )

from autogen_agentchat.agents import AssistantAgent  # noqa: E402
from autogen_core.memory import MemoryContent, MemoryMimeType  # noqa: E402
from autogen_ext.models.openai import OpenAIChatCompletionClient  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_autogen import ZepUserMemory, create_thread  # noqa: E402

_suffix = uuid4().hex[:8]

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"
# The v4 server cannot add a message to a thread whose user has no ``user_id``,
# so the live test creates the user with a unique label through the SDK. The
# package API stays UUID-only, and ``create_user`` keeps its unit-test cover.
USER_LABEL = f"integtest-{_suffix}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_integration")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


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
    timeout_seconds: int = 120,
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
            episodes = [episode async for episode in pager]
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            await asyncio.sleep(poll_interval)
            continue
        if episodes and all(episode.processed for episode in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent(
    zep: AsyncZep, user_uuid: str, thread_uuid: str
) -> tuple[AssistantAgent, ZepUserMemory]:
    """Build an AutoGen assistant wired to Zep memory on the given thread."""
    memory = ZepUserMemory(client=zep, user_uuid=user_uuid, thread_uuid=thread_uuid)
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model=OPENAI_MODEL),
        memory=[memory],
        system_message=(
            "You are a helpful assistant with long-term memory. Use any injected "
            "context to answer questions about the user. Be concise."
        ),
    )
    return agent, memory


async def store_turn(
    memory: ZepUserMemory, content: str, role: str, name: str | None = None
) -> None:
    """Persist a single conversation turn to Zep via the integration."""
    await memory.add(
        MemoryContent(
            content=content,
            mime_type=MemoryMimeType.TEXT,
            metadata={"type": "message", "role": role, "name": name},
        )
    )


async def collect_messages(zep: AsyncZep, thread_uuid: str, limit: int = 20) -> list:
    """Return up to ``limit`` messages of a thread."""
    pager = await zep.thread.list_messages(thread_uuid, limit=limit)
    return [message async for message in pager]


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True
    user_uuid = ""

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        user = await zep.user.create(
            user_id=USER_LABEL, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL
        )
        user_uuid = str(user.uuid_)
        graph_uuid = str(user.graph_uuid)
        thread1 = await create_thread(zep, user_uuid=user_uuid)
        thread1_uuid = str(thread1.uuid_)

        print(f"\n{'=' * 70}")
        print("Zep AutoGen Integration Test")
        print(f"  User:   {user_uuid}")
        print(f"  Thread: {thread1_uuid}")
        print(f"{'=' * 70}\n")

        # -- Conversation 1: seed facts via the integration. -----------------
        print("[Step 1] Conversation 1: seeding facts...")
        _agent1, memory1 = build_agent(zep, user_uuid, thread1_uuid)
        seeds = [
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ]
        for msg in seeds:
            print(f"  User:  {msg}")
            await store_turn(memory1, msg, "user", FIRST_NAME)
            response = await _agent1.run(task=msg)
            reply = str(response.messages[-1].content)
            print(f"  Agent: {reply}\n")
            await store_turn(memory1, reply, "assistant")
            passed &= check("Agent returned a non-empty response", len(reply) > 0)

        # -- Verify user metadata --------------------------------------------
        print("[Step 2] Verifying Zep user metadata...")
        stored_user = await zep.user.get(user_uuid)
        passed &= check(
            "first_name matches", stored_user.first_name == FIRST_NAME, str(stored_user.first_name)
        )
        passed &= check(
            "last_name matches", stored_user.last_name == LAST_NAME, str(stored_user.last_name)
        )
        passed &= check("email matches", stored_user.email == EMAIL, str(stored_user.email))

        # -- Verify thread 1 captured both sides -----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        messages = await collect_messages(zep, thread1_uuid)
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ----------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=120)

        # -- Conversation 2: cross-thread memory recall ----------------------
        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        thread2 = await create_thread(zep, user_uuid=user_uuid)
        agent2, memory2 = build_agent(zep, user_uuid, str(thread2.uuid_))
        question = "What do you know about me?"
        await store_turn(memory2, question, "user", FIRST_NAME)
        response = await agent2.run(task=question)
        recall = str(response.messages[-1].content).lower()
        print(f"  Agent: {recall}\n")
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        found = [kw for kw in keywords if kw in recall]
        print(f"  Recalled keywords: {found}")
        passed &= check(
            "Agent recalled facts from conversation 1",
            len(found) > 0,
            f"found={found}",
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live integration test."""
    zep = AsyncZep(api_key=ZEP_API_KEY)
    user_uuid = ""

    try:
        user = await zep.user.create(
            user_id=USER_LABEL, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL
        )
        user_uuid = str(user.uuid_)
        graph_uuid = str(user.graph_uuid)
        thread1 = await create_thread(zep, user_uuid=user_uuid)

        agent1, memory1 = build_agent(zep, user_uuid, str(thread1.uuid_))
        for msg in (
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ):
            await store_turn(memory1, msg, "user", FIRST_NAME)
            response = await agent1.run(task=msg)
            await store_turn(memory1, str(response.messages[-1].content), "assistant")

        stored_user = await zep.user.get(user_uuid)
        assert stored_user.first_name == FIRST_NAME
        assert stored_user.email == EMAIL

        messages = await collect_messages(zep, str(thread1.uuid_))
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=120)

        thread2 = await create_thread(zep, user_uuid=user_uuid)
        agent2, memory2 = build_agent(zep, user_uuid, str(thread2.uuid_))
        question = "What do you know about me?"
        await store_turn(memory2, question, "user", FIRST_NAME)
        response = await agent2.run(task=question)
        recall = str(response.messages[-1].content).lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            if user_uuid:
                await zep.user.delete(user_uuid)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
