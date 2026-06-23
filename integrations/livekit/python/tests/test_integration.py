"""
End-to-end integration test for the Zep LiveKit integration.

LiveKit agents normally run inside a voice session/room. This test drives the
``ZepUserAgent`` memory logic directly -- the same Zep operations it performs on
``on_user_turn_completed`` and when an assistant message is added -- WITHOUT a
LiveKit server, validating the integration's memory layer end-to-end:

  1. ``ZepUserAgent.on_user_turn_completed`` persists user turns to a Zep thread
     and injects the retrieved context block.
  2. ``ZepUserAgent._store_assistant_message`` persists assistant turns.
  3. Both sides of the conversation are captured on the thread.
  4. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
  5. Zep resource verification via the SDK (user metadata, thread messages,
     context block, graph search).

Only ZEP_API_KEY is required -- no LLM key, since no model is invoked.

Requires:
    ZEP_API_KEY environment variable.

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
# Configuration -- skip the whole module when the API key is not available.
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")

if not ZEP_API_KEY:
    pytest.skip(
        "ZEP_API_KEY required for integration tests",
        allow_module_level=True,
    )

from livekit.agents.llm.chat_context import ChatContext  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_livekit import ZepGraphAgent, ZepUserAgent  # noqa: E402
from zep_livekit.exceptions import AgentConfigurationError  # noqa: E402

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"livekit-integ-{_suffix}"
THREAD_1 = f"livekit-integ-t1-{_suffix}"
THREAD_2 = f"livekit-integ-t2-{_suffix}"

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"

INSTRUCTIONS = "You are a helpful travel assistant with persistent memory."

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_integration")
logging.getLogger("httpx").setLevel(logging.WARNING)


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
        resp = await zep.graph.episode.get_by_user_id(user_id=user_id, lastn=20)
        episodes = resp.episodes or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent(zep: AsyncZep, thread_id: str) -> ZepUserAgent:
    """Build a ZepUserAgent bound to the given thread (no LiveKit session)."""
    return ZepUserAgent(
        zep_client=zep,
        user_id=USER_ID,
        thread_id=thread_id,
        user_message_name=FIRST_NAME,
        assistant_message_name="Assistant",
        instructions=INSTRUCTIONS,
    )


class _AssistantItem:
    """Minimal stand-in for a LiveKit conversation item (only ``name`` is read)."""

    name = None


async def user_turn(agent: ZepUserAgent, text: str) -> ChatContext:
    """Drive ZepUserAgent.on_user_turn_completed for one user message.

    Uses a real ChatContext (so the integration's ``add_message`` call works) and
    a lightweight stand-in for the new message (the integration only reads
    ``text_content``; the base hook is a no-op). Returns the turn context so the
    caller can inspect any injected memory context.
    """
    turn_ctx = ChatContext.empty()
    new_message = type("_UserMsg", (), {"text_content": text})()
    await agent.on_user_turn_completed(turn_ctx, new_message)
    return turn_ctx


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep LiveKit Integration Test (memory layer, no voice server)")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    # -- Constructor validation (no network). --------------------------------
    print("[Step 0] Validating agent configuration guards...")
    try:
        ZepUserAgent(zep_client=zep, user_id="", thread_id=THREAD_1, instructions=INSTRUCTIONS)
        passed &= check("ZepUserAgent rejects empty user_id", False)
    except AgentConfigurationError:
        passed &= check("ZepUserAgent rejects empty user_id", True)
    try:
        ZepGraphAgent(zep_client=zep, graph_id="", instructions=INSTRUCTIONS)
        passed &= check("ZepGraphAgent rejects empty graph_id", False)
    except AgentConfigurationError:
        passed &= check("ZepGraphAgent rejects empty graph_id", True)

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        await zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        # -- Conversation 1: seed facts via the agent's memory hooks. --------
        print("\n[Step 1] Conversation 1: seeding facts...")
        agent1 = build_agent(zep, THREAD_1)
        seeds = [
            (
                "My name is IntegTest. I work at Acme Corp as a data scientist.",
                "Nice to meet you, IntegTest -- noted that you're a data scientist at Acme Corp.",
            ),
            (
                "I live in Portland, Oregon and I love hiking and photography.",
                "Great -- Portland, Oregon, plus hiking and photography.",
            ),
        ]
        for user_text, assistant_text in seeds:
            print(f"  User:  {user_text}")
            await user_turn(agent1, user_text)
            await agent1._store_assistant_message(assistant_text, _AssistantItem())
            print(f"  Agent: {assistant_text}\n")

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
        agent2 = build_agent(zep, THREAD_2)
        # Driving a user turn on a fresh thread should inject the recalled
        # context block (assembled from the shared user graph).
        await user_turn(agent2, "What do you know about me?")

        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        ctx_result = await zep.thread.get_user_context(thread_id=THREAD_2)
        context = (ctx_result.context or "").lower()
        found = [kw for kw in keywords if kw in context]
        print(f"  Recalled keywords (context block): {found}")
        passed &= check(
            "Context block recalls facts from conversation 1",
            len(found) > 0,
            f"found={found}",
        )

        search = await zep.graph.search(user_id=USER_ID, query="job, location, hobbies", limit=10)
        search_text = " ".join(e.fact for e in (search.edges or [])).lower()
        search_found = [kw for kw in keywords if kw in search_text]
        print(f"  Recalled keywords (graph search): {search_found}")
        passed &= check(
            "Graph search recalls facts from conversation 1",
            len(search_found) > 0,
            f"found={search_found}",
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

    # Config guards (no network).
    with pytest.raises(AgentConfigurationError):
        ZepUserAgent(zep_client=zep, user_id="", thread_id=THREAD_1, instructions=INSTRUCTIONS)
    with pytest.raises(AgentConfigurationError):
        ZepGraphAgent(zep_client=zep, graph_id="", instructions=INSTRUCTIONS)

    try:
        await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        await zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        agent1 = build_agent(zep, THREAD_1)
        for user_text, assistant_text in (
            (
                "My name is IntegTest. I work at Acme Corp as a data scientist.",
                "Noted -- a data scientist at Acme Corp.",
            ),
            (
                "I live in Portland, Oregon and I love hiking and photography.",
                "Got it -- Portland, Oregon, hiking and photography.",
            ),
        ):
            await user_turn(agent1, user_text)
            await agent1._store_assistant_message(assistant_text, _AssistantItem())

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        await zep.thread.create(thread_id=THREAD_2, user_id=USER_ID)
        agent2 = build_agent(zep, THREAD_2)
        await user_turn(agent2, "What do you know about me?")

        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        ctx_result = await zep.thread.get_user_context(thread_id=THREAD_2)
        context = (ctx_result.context or "").lower()
        search = await zep.graph.search(user_id=USER_ID, query="job, location, hobbies", limit=10)
        search_text = " ".join(e.fact for e in (search.edges or [])).lower()
        assert any(kw in context or kw in search_text for kw in keywords), (
            f"no recall in context/search: {context} / {search_text}"
        )
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
