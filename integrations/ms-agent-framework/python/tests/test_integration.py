"""
End-to-end integration test for the Zep Microsoft Agent Framework integration.

Exercises the full lifecycle against live Zep and OpenAI:

  1. Out-of-band user and thread creation, which returns the UUIDs.
  2. ``before_run`` persists the user turn; ``after_run`` persists the assistant
     turn (both sides captured on the thread).
  3. The ``on_created`` hook of ``create_user`` fires one time.
  4. Cross-thread memory recall: a second thread for the same user recalls facts
     seeded in the first thread (proving recall comes from the user graph).
  5. Zep resource verification via the SDK (user metadata, thread messages).

Also includes a lighter-weight test (``test_provisioning_and_before_after_run``)
that only requires ``ZEP_API_KEY`` -- it drives ``create_user``/``create_thread``
and a ``before_run``/``after_run`` cycle directly against real Zep with a fake
session double, without needing a real model provider.

Requires:
    ZEP_API_KEY (all tests) and OPENAI_API_KEY (agent-driven tests only).

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
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Configuration -- skip the whole module when no Zep API key is available.
# Tests that also need a real model provider additionally gate on
# OPENAI_API_KEY (see test_integration_full_lifecycle).
# ---------------------------------------------------------------------------
ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

if not ZEP_API_KEY:
    pytest.skip("ZEP_API_KEY required for integration tests", allow_module_level=True)

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_ms_agent_framework import (  # noqa: E402
    ZepContextProvider,
    create_thread,
    create_user,
)

_suffix = uuid4().hex[:8]

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"


def new_user_label() -> str:
    """Return a unique ``user_id`` label for a live test user.

    The v4 server cannot add a message to a thread whose user has no
    ``user_id``, so the live test gives each user a label. The package API
    stays UUID-only.
    """
    return f"integtest-{uuid4().hex[:8]}"


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
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            await asyncio.sleep(poll_interval)
            continue
        episodes = pager.items or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        await asyncio.sleep(poll_interval)


def build_agent(zep: AsyncZep, user_uuid: str, thread_uuid: str) -> Agent:
    """Build an agent scoped to the given user and thread UUIDs."""
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
                user_uuid=user_uuid,
                thread_uuid=thread_uuid,
            )
        ],
    )


async def main() -> None:
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not set; skipping the agent-driven lifecycle test.")
        sys.exit(0)

    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_uuid: str) -> None:
        hook_calls.append(user_uuid)
        logger.info("on_created fired for %s", user_uuid)

    user = await create_user(
        zep,
        user_id=new_user_label(),
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=EMAIL,
        on_created=on_user_created,
    )
    user_uuid = str(user.uuid_)
    graph_uuid = str(user.graph_uuid)
    thread_1 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)
    thread_2 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)

    print(f"\n{'=' * 70}")
    print("Zep Microsoft Agent Framework Integration Test")
    print(f"  User:    {user_uuid}")
    print(f"  Threads: {thread_1}, {thread_2}")
    print(f"{'=' * 70}\n")

    try:
        # -- Conversation 1: seed facts -------------------------------------
        print("[Step 1] Conversation 1: seeding facts...")
        agent1 = build_agent(zep, user_uuid, thread_1)
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
            "on_created hook fired one time",
            hook_calls == [user_uuid],
            f"calls={hook_calls}",
        )

        # -- Verify user metadata -------------------------------------------
        print("[Step 2] Verifying Zep user metadata...")
        fetched = await zep.user.get(user_uuid)
        passed &= check(
            "first_name matches", fetched.first_name == FIRST_NAME, str(fetched.first_name)
        )
        passed &= check("last_name matches", fetched.last_name == LAST_NAME, str(fetched.last_name))
        passed &= check("email matches", fetched.email == EMAIL, str(fetched.email))

        # -- Verify thread 1 captured both sides ----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        pager = await zep.thread.list_messages(thread_1, limit=20)
        messages = pager.items or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ---------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=120)

        # -- Conversation 2: cross-thread recall ----------------------------
        print("\n[Step 5] Conversation 2: cross-thread memory recall...")
        agent2 = build_agent(zep, user_uuid, thread_2)
        result = await agent2.run("What do you know about me?")
        print(f"  Agent: {result.text}\n")

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
        "ZEPAI-3605: a user created without a user_id cannot receive a thread "
        "message. Enable this test after the fix is deployed."
    )
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_provisioning_and_before_after_run() -> None:
    """Exercise ``create_user``/``create_thread`` and a ``before_run``/
    ``after_run`` cycle directly against real Zep, without a model provider.

    Uses a fake ``SessionContext`` double (mirroring the mock-based unit
    tests) so this only requires ``ZEP_API_KEY`` -- no OpenAI call is made.
    """
    zep = AsyncZep(api_key=ZEP_API_KEY)
    hook_calls: list[str] = []

    async def on_user_created(client: AsyncZep, user_uuid: str) -> None:
        hook_calls.append(user_uuid)

    user = await create_user(
        zep,
        user_id=new_user_label(),
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=f"provision-{_suffix}@example.com",
        on_created=on_user_created,
    )
    user_uuid = str(user.uuid_)
    assert hook_calls == [user_uuid]
    assert user.graph_uuid

    thread = await create_thread(zep, user_uuid=user_uuid)
    thread_uuid = str(thread.uuid_)
    assert thread_uuid

    try:
        provider = ZepContextProvider(
            zep_client=zep,
            user_uuid=user_uuid,
            thread_uuid=thread_uuid,
            graph_uuid=user.graph_uuid,
        )

        before_ctx = MagicMock()
        before_ctx.input_messages = [
            MagicMock(role="user", text="Hello from the integration test.")
        ]
        before_ctx.extend_instructions = MagicMock()
        before_ctx.extend_tools = MagicMock()
        before_ctx.response = None

        await provider.before_run(
            agent=MagicMock(), session=MagicMock(), context=before_ctx, state={}
        )
        assert provider._user_turn_persisted is True

        after_ctx = MagicMock()
        after_ctx.input_messages = []
        response = MagicMock()
        response.messages = [MagicMock(role="assistant", text="Hi there!")]
        after_ctx.response = response

        await provider.after_run(
            agent=MagicMock(), session=MagicMock(), context=after_ctx, state={}
        )

        # -- Context retrieval round-trips: the thread now has both turns ---
        pager = await zep.thread.list_messages(thread_uuid, limit=20)
        messages = pager.items or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)
    finally:
        try:
            await zep.user.delete(user_uuid)
        except Exception:
            pass


@pytest.mark.skip(
    reason=(
        "ZEPAI-3605: a user created without a user_id cannot receive a thread "
        "message. Enable this test after the fix is deployed."
    )
)
@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live integration test."""
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY required for this test")

    zep = AsyncZep(api_key=ZEP_API_KEY)

    user = await create_user(
        zep,
        user_id=new_user_label(),
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=EMAIL,
    )
    user_uuid = str(user.uuid_)
    graph_uuid = str(user.graph_uuid)
    thread_1 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)
    thread_2 = str((await create_thread(zep, user_uuid=user_uuid)).uuid_)

    try:
        agent1 = build_agent(zep, user_uuid, thread_1)
        await agent1.run("My name is IntegTest. I work at Acme Corp as a data scientist.")
        await agent1.run("I live in Portland, Oregon and love hiking and photography.")

        fetched = await zep.user.get(user_uuid)
        assert fetched.first_name == FIRST_NAME
        assert fetched.email == EMAIL

        pager = await zep.thread.list_messages(thread_1, limit=20)
        messages = pager.items or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, graph_uuid, timeout_seconds=120)

        agent2 = build_agent(zep, user_uuid, thread_2)
        result = await agent2.run("What do you know about me?")

        recall = result.text.lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {result.text}"
    finally:
        try:
            await zep.user.delete(user_uuid)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
