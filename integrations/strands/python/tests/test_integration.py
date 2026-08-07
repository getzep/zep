"""
End-to-end integration tests for the Zep Strands Agents integration.

Two layers:

  1. ``test_store_round_trip`` — store-only against live Zep (``ZEP_API_KEY``).
     Exercises ``ensure_user`` / ``ensure_thread``, ``add_messages``, ``add``,
     and ``search`` without a model provider.
  2. ``test_integration_full_lifecycle`` — full agent loop against live Zep and
     OpenAI (``ZEP_API_KEY`` + ``OPENAI_API_KEY``):
       * ``MemoryManager`` + ``ZepMemoryStore`` inject context and extract turns
       * Both sides of the conversation land on the thread after ``flush()``
       * Cross-thread recall from the user graph
       * ``on_user_created`` fires exactly once

Requires:
    ZEP_API_KEY (all tests) and OPENAI_API_KEY (agent-driven tests only).

Usage:
    uv run pytest tests/test_integration.py -v -s -m integration
    # or standalone (agent lifecycle):
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
# Configuration — skip the module when no Zep API key is available.
# Agent-driven tests additionally gate on OPENAI_API_KEY.
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

if not ZEP_API_KEY:
    pytest.skip("ZEP_API_KEY required for integration tests", allow_module_level=True)

from strands import Agent  # noqa: E402
from strands.memory import MemoryManager  # noqa: E402
from strands.models.openai import OpenAIModel  # noqa: E402
from strands.types.content import Message  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_strands import (  # noqa: E402
    ZepMemoryStore,
    ensure_thread,
    ensure_user,
)
from zep_strands.provisioning import UserSetupHook  # noqa: E402

pytestmark = pytest.mark.integration

_suffix = uuid4().hex[:8]
USER_ID = f"strands-integ-{_suffix}"
THREAD_1 = f"strands-integ-t1-{_suffix}"
THREAD_2 = f"strands-integ-t2-{_suffix}"

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


def _message_text(message: Message | object) -> str:
    """Extract joined text blocks from an agent result message."""
    if not isinstance(message, dict):
        return str(message)
    parts = [
        block["text"]
        for block in message.get("content") or []
        if isinstance(block, dict) and "text" in block
    ]
    return "\n".join(parts) if parts else str(message)


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


async def build_agent(
    zep: AsyncZep,
    thread_id: str,
    *,
    on_user_created: UserSetupHook | None = None,
) -> Agent:
    """Build an agent whose memory is scoped to USER_ID on the given thread.

    Configured exactly as the example is: default extraction cadence, with
    callers flushing at the session boundary.
    """
    await ensure_user(
        zep,
        user_id=USER_ID,
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=EMAIL,
        on_created=on_user_created,
    )
    await ensure_thread(zep, thread_id=thread_id, user_id=USER_ID)

    store = ZepMemoryStore(
        zep_client=zep,
        user_id=USER_ID,
        thread_id=thread_id,
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=EMAIL,
        writable=True,
        extraction=True,
        expose_search_tool=True,
        search_pinned_params={"scope": "auto"},
    )
    return Agent(
        model=OpenAIModel(client_args={"api_key": OPENAI_API_KEY}, model_id=OPENAI_MODEL),
        system_prompt=(
            "You are a helpful assistant with access to long-term memory. "
            "When memory context is provided, use it to give personalised, "
            "memory-aware answers. Be concise."
        ),
        memory_manager=MemoryManager(stores=[store], add_tool_config=True),
    )


async def chat(agent: Agent, message: str) -> str:
    """Send one message to the agent and return its text reply."""
    result = await agent.invoke_async(message)
    return _message_text(result.message)


async def flush(agent: Agent) -> None:
    """Force buffered extraction writes out at a session boundary.

    ``invoke_async`` never flushes on its own, and the default trigger only
    fires every 5 turns, so without this the seeded turns never reach Zep.
    Flushing also drains in-flight background saves before the agent is
    discarded.
    """
    if agent.memory_manager is not None:
        await agent.memory_manager.flush()


# ---------------------------------------------------------------------------
# Store-only path (ZEP_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_round_trip() -> None:
    """Exercise store methods against live Zep without a model provider."""
    zep = AsyncZep(api_key=ZEP_API_KEY)
    user_id = f"{USER_ID}-store"
    thread_id = f"{THREAD_1}-store"

    try:
        created_user = await ensure_user(
            zep,
            user_id=user_id,
            first_name=FIRST_NAME,
            last_name=LAST_NAME,
            email=f"store-{_suffix}@example.com",
        )
        assert created_user is True
        assert await ensure_user(zep, user_id=user_id) is False

        created_thread = await ensure_thread(zep, thread_id=thread_id, user_id=user_id)
        assert created_thread is True

        store = ZepMemoryStore(
            zep_client=zep,
            user_id=user_id,
            thread_id=thread_id,
            first_name=FIRST_NAME,
            last_name=LAST_NAME,
            writable=True,
            extraction=True,
            search_scope="edges",
        )

        await store.add_messages(
            [
                {
                    "role": "user",
                    "content": [{"text": "I am a ceramicist based in Santa Fe."}],
                },
                {
                    "role": "assistant",
                    "content": [{"text": "I'll remember that."}],
                },
            ]
        )
        await store.add("Prefers matte glazes over glossy finishes.")

        # Search may be empty immediately (async ingestion); just assert no raise.
        entries = await store.search("ceramics")
        assert isinstance(entries, list)

        t = await zep.thread.get(thread_id=thread_id, lastn=20)
        messages = t.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)
    finally:
        try:
            await zep.user.delete(user_id=user_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Full agent lifecycle (ZEP_API_KEY + OPENAI_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live agent integration test."""
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY required for this test")

    zep = AsyncZep(api_key=ZEP_API_KEY)
    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_id: str) -> None:
        hook_calls.append(user_id)

    try:
        agent1 = await build_agent(zep, THREAD_1, on_user_created=on_user_created)
        reply1 = await chat(
            agent1, "My name is IntegTest. I work at Acme Corp as a data scientist."
        )
        reply2 = await chat(agent1, "I live in Portland, Oregon and I love hiking and photography.")
        await flush(agent1)
        assert reply1
        assert reply2
        assert hook_calls == [USER_ID]

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.last_name == LAST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        agent2 = await build_agent(zep, THREAD_2, on_user_created=on_user_created)
        recall = (await chat(agent2, "What do you know about me?")).lower()
        await flush(agent2)

        # Existing user — hook must not fire again.
        assert hook_calls == [USER_ID]

        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


async def main() -> None:
    """Standalone runner for the agent lifecycle (mirrors pytest asserts)."""
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not set; skipping the agent-driven lifecycle test.")
        sys.exit(0)

    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True
    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_id: str) -> None:
        hook_calls.append(user_id)
        logger.info("on_user_created fired for %s", user_id)

    print(f"\n{'=' * 70}")
    print("Zep Strands Agents Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    try:
        print("[Step 1] Conversation 1: seeding facts...")
        agent1 = await build_agent(zep, THREAD_1, on_user_created=on_user_created)
        seeds = [
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ]
        for msg in seeds:
            print(f"  User:  {msg}")
            reply = await chat(agent1, msg)
            print(f"  Agent: {reply}\n")
            passed &= check("Agent returned a non-empty response", len(reply) > 0)

        await flush(agent1)

        passed &= check(
            "on_user_created hook fired exactly once",
            len(hook_calls) == 1 and hook_calls[0] == USER_ID,
            f"calls={hook_calls}",
        )

        print("[Step 2] Verifying Zep user metadata...")
        user = await zep.user.get(user_id=USER_ID)
        passed &= check("first_name matches", user.first_name == FIRST_NAME, str(user.first_name))
        passed &= check("last_name matches", user.last_name == LAST_NAME, str(user.last_name))
        passed &= check("email matches", user.email == EMAIL, str(user.email))

        print("\n[Step 3] Verifying thread 1 messages...")
        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        agent2 = await build_agent(zep, THREAD_2, on_user_created=on_user_created)
        recall_text = await chat(agent2, "What do you know about me?")
        print(f"  Agent: {recall_text}\n")
        await flush(agent2)

        passed &= check(
            "on_user_created did NOT fire again for existing user",
            len(hook_calls) == 1,
            f"calls={len(hook_calls)}",
        )

        recall = recall_text.lower()
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


if __name__ == "__main__":
    asyncio.run(main())
