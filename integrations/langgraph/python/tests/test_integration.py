"""
End-to-end integration test for the Zep LangGraph integration.

Exercises the full lifecycle against live Zep and OpenAI using the primary
node/tool helpers with a prebuilt ``create_react_agent``:

  1. ``build_system_message`` injects the Zep Context Block on every turn.
  2. ``persist_messages`` writes each turn back to the Zep thread.
  3. Both sides of the conversation are captured on the thread.
  4. User-graph recall: the integration's search tool retrieves facts extracted
     from the conversation (proving recall comes from the user graph).
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
from zep_cloud import AddMessage  # noqa: E402
from zep_cloud.client import AsyncZep  # noqa: E402

from zep_langgraph import (  # noqa: E402
    build_system_message,
    create_graph_search_tool,
    create_thread,
    create_user,
    persist_messages,
)

# Unique IDs per run to avoid collisions.
FIRST_NAME = "IntegTest"
LAST_NAME = "User"
_suffix = uuid4().hex[:8]
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


async def wait_for_graph_searchable(
    zep: AsyncZep,
    graph_uuid: str,
    query: str,
    timeout_seconds: int = 300,
    poll_interval: float = 5.0,
) -> bool:
    """Poll V4 edge search until it returns at least one result.

    Gate the recall turn on the signal it depends on: a live graph search
    returning results.
    """
    start = time.monotonic()
    while time.monotonic() - start <= timeout_seconds:
        try:
            result = await zep.graph.search_edges(graph_uuid, query=query, page_size=5)
            if result.items:
                logger.info("Graph search returned %d edges.", len(result.items))
                return True
        except Exception as exc:
            logger.warning("Graph search poll failed (%s); retrying.", exc)
        await asyncio.sleep(poll_interval)
    logger.warning("Timed out waiting for graph search results; continuing.")
    return False


def build_agent(
    zep: AsyncZep, thread_uuid: str, graph_uuid: str
) -> Callable[[str], Awaitable[str]]:
    """Build a ReAct agent wired to Zep on the given thread; return a chat fn."""

    async def prompt(state: dict) -> list:
        system = await build_system_message(
            zep, thread_uuid=thread_uuid, base_instructions=BASE_INSTRUCTIONS
        )
        return [system, *state["messages"]]

    search_tool = create_graph_search_tool(zep, graph_uuid=graph_uuid, scope="edges")
    model = ChatOpenAI(model=OPENAI_MODEL)
    agent = create_react_agent(model=model, tools=[search_tool], prompt=prompt)

    async def chat(user_text: str) -> str:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_text)]})
        reply = result["messages"][-1]
        reply_text = reply.content if isinstance(reply.content, str) else str(reply.content)
        await persist_messages(
            zep,
            thread_uuid=thread_uuid,
            messages=[
                AddMessage(role="user", content=user_text, name=f"{FIRST_NAME} {LAST_NAME}"),
                AIMessage(content=reply_text),
            ],
        )
        return reply_text

    return chat


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)
    passed = True
    user = await create_user(zep, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
    thread = await create_thread(zep, user_uuid=user.uuid_)

    print(f"\n{'=' * 70}")
    print("Zep LangGraph Integration Test")
    print(f"  User:    {user.uuid_}")
    print(f"  Thread:  {thread.uuid_}")
    print(f"{'=' * 70}\n")

    try:
        # -- Conversation 1: seed facts via the agent. -----------------------
        print("[Step 1] Conversation 1: seeding facts...")
        chat1 = build_agent(zep, thread.uuid_, user.uuid_)
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
        fetched_user = await zep.user.get(user_uuid=user.uuid_)
        passed &= check(
            "first_name matches",
            fetched_user.first_name == FIRST_NAME,
            str(fetched_user.first_name),
        )
        passed &= check(
            "last_name matches", fetched_user.last_name == LAST_NAME, str(fetched_user.last_name)
        )
        passed &= check("email matches", fetched_user.email == EMAIL, str(fetched_user.email))

        # -- Verify thread 1 captured both sides -----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        page = await zep.thread.list_messages(thread_uuid=thread.uuid_, page_size=20)
        messages = page.items or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 2, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ----------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        searchable = await wait_for_graph_searchable(
            zep, user.uuid_, query="Where does IntegTest work?"
        )
        passed &= check("User graph is searchable", searchable)

        # -- Recall through the integration's graph-search tool --------------
        print("\n[Step 5] Recalling facts through search_memory...")
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        search_tool = create_graph_search_tool(zep, graph_uuid=user.uuid_, scope="edges")
        recall = str(await search_tool.ainvoke({"query": "Where does IntegTest work?"})).lower()
        found = [kw for kw in keywords if kw in recall]
        print(f"  Search results: {recall}\n")
        print(f"  Recalled keywords: {found}")
        passed &= check(
            "Search tool recalled facts from conversation 1",
            len(found) > 0,
            f"found={found}",
        )

    finally:
        print("\n[Cleanup] Deleting test thread and user...")
        try:
            await zep.thread.delete(thread_uuid=thread.uuid_)
            await zep.user.delete(user_uuid=user.uuid_)
            print(f"  Deleted {user.uuid_}")
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
    user = await create_user(zep, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
    thread = await create_thread(zep, user_uuid=user.uuid_)

    try:
        chat1 = build_agent(zep, thread.uuid_, user.uuid_)
        # One combined turn on purpose: each persist_messages call creates one
        # Zep extraction episode, and a user's episodes process serially, so
        # extra turns multiply the live-test ingestion wait.
        await chat1(
            "My name is IntegTest. I work at Acme Corp as a data scientist. "
            "I live in Portland, Oregon and I love hiking and photography."
        )

        fetched_user = await zep.user.get(user_uuid=user.uuid_)
        assert fetched_user.first_name == FIRST_NAME
        assert fetched_user.email == EMAIL

        page = await zep.thread.list_messages(thread_uuid=thread.uuid_, page_size=20)
        messages = page.items or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        assert await wait_for_graph_searchable(
            zep, user.uuid_, query="Where does IntegTest work?"
        ), "user graph never became searchable"

        # Exercise the integration's tool directly. Whether an LLM elects to
        # call an optional tool is nondeterministic and is not a reliable signal
        # for whether ingestion and graph recall work.
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        search_tool = create_graph_search_tool(zep, graph_uuid=user.uuid_, scope="edges")
        recall = str(await search_tool.ainvoke({"query": "Where does IntegTest work?"})).lower()
        assert any(kw in recall for kw in keywords), f"no recall in: {recall}"
    finally:
        try:
            await zep.thread.delete(thread_uuid=thread.uuid_)
            await zep.user.delete(user_uuid=user.uuid_)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
