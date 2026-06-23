"""
End-to-end integration test for the Zep LangGraph integration.

Exercises the full lifecycle against live Zep and OpenAI using the primary
node/tool helpers with a prebuilt ``create_react_agent``:

  1. ``build_system_message`` injects the Zep Context Block on every turn.
  2. ``persist_messages`` writes each turn back to the Zep thread.
  3. Both sides of the conversation are captured on the thread.
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
from collections.abc import Awaitable, Callable
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

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402
from zep_cloud import Message  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_langgraph import (  # noqa: E402
    build_system_message,
    create_graph_search_tool,
    persist_messages,
)

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"langgraph-integ-{_suffix}"
THREAD_1 = f"langgraph-integ-t1-{_suffix}"
THREAD_2 = f"langgraph-integ-t2-{_suffix}"

FIRST_NAME = "IntegTest"
LAST_NAME = "User"
EMAIL = f"integtest-{_suffix}@example.com"

BASE_INSTRUCTIONS = (
    "You are a helpful assistant with long-term memory. When memory context is "
    "provided, use it to give personalised, memory-aware answers. You may also call "
    "the search_memory tool to look up specific details on demand."
)

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


def build_agent(zep: AsyncZep, thread_id: str) -> Callable[[str], Awaitable[str]]:
    """Build a ReAct agent wired to Zep on the given thread; return a chat fn."""

    async def prompt(state: dict) -> list:
        system = await build_system_message(
            zep, thread_id=thread_id, base_instructions=BASE_INSTRUCTIONS
        )
        return [system, *state["messages"]]

    search_tool = create_graph_search_tool(zep, user_id=USER_ID, scope="edges")
    model = ChatOpenAI(model=OPENAI_MODEL)
    agent = create_react_agent(model=model, tools=[search_tool], prompt=prompt)

    async def chat(user_text: str) -> str:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_text)]})
        reply = result["messages"][-1]
        reply_text = reply.content if isinstance(reply.content, str) else str(reply.content)
        await persist_messages(
            zep,
            thread_id=thread_id,
            messages=[
                Message(role="user", content=user_text, name=f"{FIRST_NAME} {LAST_NAME}"),
                AIMessage(content=reply_text),
            ],
        )
        return reply_text

    return chat


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep LangGraph Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        await zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        # -- Conversation 1: seed facts via the agent. -----------------------
        print("[Step 1] Conversation 1: seeding facts...")
        chat1 = build_agent(zep, THREAD_1)
        seeds = [
            "My name is IntegTest. I work at Acme Corp as a data scientist.",
            "I live in Portland, Oregon and I love hiking and photography.",
        ]
        for msg in seeds:
            print(f"  User:  {msg}")
            reply = await chat1(msg)
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
        await zep.thread.create(thread_id=THREAD_2, user_id=USER_ID)
        chat2 = build_agent(zep, THREAD_2)
        recall = (await chat2("What do you know about me?")).lower()
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

        chat1 = build_agent(zep, THREAD_1)
        await chat1("My name is IntegTest. I work at Acme Corp as a data scientist.")
        await chat1("I live in Portland, Oregon and I love hiking and photography.")

        user = await zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = await zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        await wait_for_episodes_processed(zep, USER_ID, timeout_seconds=120)

        await zep.thread.create(thread_id=THREAD_2, user_id=USER_ID)
        chat2 = build_agent(zep, THREAD_2)
        recall = (await chat2("What do you know about me?")).lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            await zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
