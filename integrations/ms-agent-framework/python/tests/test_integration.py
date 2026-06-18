"""
End-to-end integration test for the Zep Microsoft Agent Framework integration.

Exercises the full lifecycle against live Zep and OpenAI:

  1. Lazy user + thread creation with correct metadata.
  2. ``before_run`` persists the user turn; ``after_run`` persists the assistant
     turn (both sides captured on the thread).
  3. The ``on_user_created`` hook fires exactly once.
  4. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
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

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_ms_agent_framework import ZepContextProvider  # noqa: E402

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"af-integ-{_suffix}"
THREAD_1 = f"af-integ-t1-{_suffix}"
THREAD_2 = f"af-integ-t2-{_suffix}"

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


def build_agent(zep: AsyncZep, thread_id: str, hook=None) -> Agent:
    """Build an agent scoped to USER_ID on the given thread."""
    return Agent(
        OpenAIChatClient(model=OPENAI_MODEL, api_key=OPENAI_API_KEY),
        instructions=(
            "You are a helpful assistant with long-term memory. When context "
            "from Zep is provided, use it to answer questions about the user. "
            "Be concise."
        ),
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_id=USER_ID,
                thread_id=thread_id,
                first_name=FIRST_NAME,
                last_name=LAST_NAME,
                email=EMAIL,
                on_user_created=hook,
            )
        ],
    )


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep Microsoft Agent Framework Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_id: str) -> None:
        hook_calls.append(user_id)
        logger.info("on_user_created fired for %s", user_id)

    try:
        # -- Conversation 1: seed facts -------------------------------------
        print("[Step 1] Conversation 1: seeding facts...")
        agent1 = build_agent(zep, THREAD_1, hook=on_user_created)
        seeds = [
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ]
        for msg in seeds:
            print(f"  User:  {msg}")
            result = await agent1.run(msg)
            print(f"  Agent: {result.text}\n")
            passed &= check("Agent returned a non-empty response", len(result.text) > 0)

        passed &= check(
            "on_user_created hook fired exactly once",
            len(hook_calls) == 1 and hook_calls[0] == USER_ID,
            f"calls={hook_calls}",
        )

        # -- Verify user metadata -------------------------------------------
        print("[Step 2] Verifying Zep user metadata...")
        user = await zep.user.get(user_id=USER_ID)
        passed &= check("first_name matches", user.first_name == FIRST_NAME, str(user.first_name))
        passed &= check("last_name matches", user.last_name == LAST_NAME, str(user.last_name))
        passed &= check("email matches", user.email == EMAIL, str(user.email))

        # -- Verify thread 1 captured both sides ----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ---------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        # -- Conversation 2: cross-thread recall ----------------------------
        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        agent2 = build_agent(zep, THREAD_2, hook=on_user_created)
        result = await agent2.run("What do you know about me?")
        print(f"  Agent: {result.text}\n")

        passed &= check(
            "on_user_created did NOT fire again for existing user",
            len(hook_calls) == 1,
            f"calls={len(hook_calls)}",
        )

        recall = result.text.lower()
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
    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_id: str) -> None:
        hook_calls.append(user_id)

    try:
        agent1 = build_agent(zep, THREAD_1, hook=on_user_created)
        await agent1.run("My name is IntegTest. I work at Acme Corp as a data scientist.")
        await agent1.run("I live in Portland, Oregon and love hiking and photography.")

        assert hook_calls == [USER_ID]

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        agent2 = build_agent(zep, THREAD_2, hook=on_user_created)
        result = await agent2.run("What do you know about me?")

        # Existing user -> hook must not fire again.
        assert hook_calls == [USER_ID]

        recall = result.text.lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {result.text}"
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
