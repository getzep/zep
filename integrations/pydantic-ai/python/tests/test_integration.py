"""
End-to-end integration test for the Zep Pydantic AI integration.

Exercises the full lifecycle against live Zep and OpenAI:

  1. ``zep_history_processor`` persists each user turn and injects Zep's context
     block; ``persist_run`` stores the assistant reply back to the thread.
  2. Both sides of the conversation are captured on the thread.
  3. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
  4. Zep resource verification via the SDK (user metadata, thread messages).

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

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.capabilities import ProcessHistory  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_pydantic_ai import (  # noqa: E402
    ZepDeps,
    create_zep_search_tool,
    persist_run,
    zep_history_processor,
)

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"pydantic-integ-{_suffix}"
THREAD_1 = f"pydantic-integ-t1-{_suffix}"
THREAD_2 = f"pydantic-integ-t2-{_suffix}"

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
        resp = await zep.graph.episode.get_by_user_id(user_id=user_id, lastn=20)
        episodes = resp.episodes or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent() -> Agent:
    """Build the Pydantic AI agent; per-user identity is supplied via ZepDeps."""
    return Agent(
        f"openai:{OPENAI_MODEL}",
        deps_type=ZepDeps,
        capabilities=[ProcessHistory(zep_history_processor)],
        tools=[create_zep_search_tool()],
        instructions=(
            "You are a helpful assistant with access to long-term memory. When "
            "context from Zep is injected into the prompt, use it to provide "
            "personalised, memory-aware responses. Use the zep_search tool when you "
            "need to look up specific details the user shared earlier."
        ),
    )


def make_deps(zep: AsyncZep, thread_id: str) -> ZepDeps:
    return ZepDeps(
        client=zep,
        user_id=USER_ID,
        thread_id=thread_id,
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=EMAIL,
    )


async def chat(agent: Agent, deps: ZepDeps, message: str) -> str:
    """Send one message through the agent and persist the assistant reply."""
    result = await agent.run(message, deps=deps)
    await persist_run(deps, result.new_messages())
    return str(result.output)


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep Pydantic AI Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    try:
        agent = build_agent()

        # -- Conversation 1: seed facts (history processor creates user/thread).
        print("[Step 1] Conversation 1: seeding facts...")
        deps1 = make_deps(zep, THREAD_1)
        seeds = [
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ]
        for msg in seeds:
            print(f"  User:  {msg}")
            reply = await chat(agent, deps1, msg)
            print(f"  Agent: {reply}\n")
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
        deps2 = make_deps(zep, THREAD_2)
        recall = (await chat(agent, deps2, "What do you know about me?")).lower()
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
        agent = build_agent()

        deps1 = make_deps(zep, THREAD_1)
        await chat(agent, deps1, "My name is IntegTest. I work at Acme Corp as a data scientist.")
        await chat(agent, deps1, "I live in Portland, Oregon and I love hiking and photography.")

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        deps2 = make_deps(zep, THREAD_2)
        recall = (await chat(agent, deps2, "What do you know about me?")).lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
