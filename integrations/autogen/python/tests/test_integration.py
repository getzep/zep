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

from zep_autogen import ZepUserMemory  # noqa: E402

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"autogen-integ-{_suffix}"
THREAD_1 = f"autogen-integ-t1-{_suffix}"
THREAD_2 = f"autogen-integ-t2-{_suffix}"

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"

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
    user_id: str,
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
            resp = await zep.graph.episode.get_by_user_id(user_id=user_id, lastn=20)
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            await asyncio.sleep(poll_interval)
            continue
        episodes = resp.episodes or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent(zep: AsyncZep, thread_id: str) -> tuple[AssistantAgent, ZepUserMemory]:
    """Build an AutoGen assistant wired to Zep memory on the given thread."""
    memory = ZepUserMemory(client=zep, thread_id=thread_id, user_id=USER_ID)
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


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep AutoGen Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        await zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        # -- Conversation 1: seed facts via the integration. -----------------
        print("[Step 1] Conversation 1: seeding facts...")
        _agent1, memory1 = build_agent(zep, THREAD_1)
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
        user = await zep.user.get(user_id=USER_ID)
        passed &= check("first_name matches", user.first_name == FIRST_NAME, str(user.first_name))
        passed &= check("last_name matches", user.last_name == LAST_NAME, str(user.last_name))
        passed &= check("email matches", user.email == EMAIL, str(user.email))

        # -- Verify thread 1 captured both sides -----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ----------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        # -- Conversation 2: cross-thread memory recall ----------------------
        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        await zep.thread.create(thread_id=THREAD_2, user_id=USER_ID)
        agent2, memory2 = build_agent(zep, THREAD_2)
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
            await zep.user.delete(user_id=USER_ID)
            print(f"  Deleted {USER_ID}")
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

    try:
        await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        await zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        agent1, memory1 = build_agent(zep, THREAD_1)
        for msg in (
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ):
            await store_turn(memory1, msg, "user", FIRST_NAME)
            response = await agent1.run(task=msg)
            await store_turn(memory1, str(response.messages[-1].content), "assistant")

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        await zep.thread.create(thread_id=THREAD_2, user_id=USER_ID)
        agent2, memory2 = build_agent(zep, THREAD_2)
        question = "What do you know about me?"
        await store_turn(memory2, question, "user", FIRST_NAME)
        response = await agent2.run(task=question)
        recall = str(response.messages[-1].content).lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
