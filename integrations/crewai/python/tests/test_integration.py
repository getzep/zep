"""
End-to-end integration test for the Zep CrewAI integration.

Exercises the full lifecycle against live Zep and OpenAI. CrewAI is synchronous,
so this test uses the sync ``Zep`` client throughout:

  1. ``ZepUserStorage.save`` persists conversation turns and facts to Zep.
  2. Both sides of the conversation are captured on the thread.
  3. After async graph ingestion, ``ZepUserStorage.search`` recalls the seeded
     facts from the user graph (thread-independent recall).
  4. A live CrewAI agent equipped with the Zep search tool recalls those facts.
  5. Zep resource verification via the SDK (user metadata, thread messages).

Requires:
    ZEP_API_KEY and OPENAI_API_KEY environment variables.

Usage:
    uv run pytest tests/test_integration.py -v -s -m integration
    # or standalone:
    uv run python tests/test_integration.py
"""

from __future__ import annotations

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

from crewai import Agent, Crew, Process, Task  # noqa: E402
from zep_cloud.client import Zep  # noqa: E402

from zep_crewai import ZepUserStorage, create_search_tool  # noqa: E402

# Unique IDs per run to avoid collisions.
_suffix = uuid4().hex[:8]
USER_ID = f"crewai-integ-{_suffix}"
THREAD_1 = f"crewai-integ-t1-{_suffix}"
THREAD_2 = f"crewai-integ-t2-{_suffix}"

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


def wait_for_episodes_processed(
    zep: Zep,
    user_id: str,
    timeout_seconds: int = 300,
    poll_interval: float = 3.0,
) -> None:
    """Poll Zep episodes until all are processed or the timeout is reached (sync)."""
    start = time.monotonic()
    while True:
        if time.monotonic() - start > timeout_seconds:
            logger.warning("Timed out waiting for episode processing; continuing.")
            return
        try:
            resp = zep.graph.episode.get_by_user_id(user_id=user_id, lastn=20)
        except Exception as exc:
            logger.warning("Episode poll failed (%s); retrying.", exc)
            time.sleep(poll_interval)
            continue
        episodes = resp.episodes or []
        if episodes and all(e.processed for e in episodes):
            logger.info("All %d episodes processed.", len(episodes))
            return
        time.sleep(poll_interval)


def seed_facts(storage: ZepUserStorage) -> None:
    """Persist a seeded conversation through the integration's storage adapter.

    Kept to two ``save()`` calls on purpose: each call creates one Zep
    extraction episode, and episodes for a user are processed serially, so
    extra calls multiply the live-test ingestion wait.
    """
    storage.save(
        "My name is IntegTest. I work at Acme Corp as a data scientist. "
        "I live in Portland, Oregon and I love hiking and photography.",
        metadata={"type": "message", "role": "user", "name": FIRST_NAME},
    )
    storage.save(
        "Nice to meet you, IntegTest -- noted you're a data scientist at Acme "
        "Corp in Portland who enjoys hiking and photography.",
        metadata={"type": "message", "role": "assistant", "name": "Assistant"},
    )


def main() -> None:
    zep = Zep(api_key=ZEP_API_KEY)
    passed = True

    print(f"\n{'=' * 70}")
    print("Zep CrewAI Integration Test")
    print(f"  User:    {USER_ID}")
    print(f"  Threads: {THREAD_1}, {THREAD_2}")
    print(f"{'=' * 70}\n")

    try:
        # -- One-time Zep setup: create the user and thread out-of-band. ------
        zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        # -- Seed facts via the integration. ---------------------------------
        print("[Step 1] Seeding facts via ZepUserStorage...")
        storage1 = ZepUserStorage(client=zep, user_id=USER_ID, thread_id=THREAD_1)
        seed_facts(storage1)

        # -- Verify user metadata --------------------------------------------
        print("[Step 2] Verifying Zep user metadata...")
        user = zep.user.get(user_id=USER_ID)
        passed &= check("first_name matches", user.first_name == FIRST_NAME, str(user.first_name))
        passed &= check("last_name matches", user.last_name == LAST_NAME, str(user.last_name))
        passed &= check("email matches", user.email == EMAIL, str(user.email))

        # -- Verify thread 1 captured both sides -----------------------------
        print("\n[Step 3] Verifying thread 1 messages...")
        t1 = zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        user_msgs = [m for m in messages if m.role == "user"]
        asst_msgs = [m for m in messages if m.role == "assistant"]
        print(f"  {len(user_msgs)} user, {len(asst_msgs)} assistant messages")
        passed &= check("Thread 1 has user messages", len(user_msgs) >= 2, f"{len(user_msgs)}")
        passed &= check("Thread 1 has assistant messages", len(asst_msgs) >= 1, f"{len(asst_msgs)}")

        # -- Wait for graph ingestion ----------------------------------------
        print("\n[Step 4] Waiting for Zep to process episodes...")
        wait_for_episodes_processed(zep, USER_ID, timeout_seconds=300)

        # -- Recall via the integration's search (thread-independent). --------
        print("\n[Step 5] Recall via ZepUserStorage.search...")
        storage2 = ZepUserStorage(client=zep, user_id=USER_ID, thread_id=THREAD_2)
        results = storage2.search("What is IntegTest's job, location, and hobbies?", limit=10)
        recalled = str(results).lower()
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        found = [kw for kw in keywords if kw in recalled]
        print(f"  Recalled keywords (search): {found}")
        passed &= check(
            "Zep search recalls seeded facts",
            len(found) > 0,
            f"found={found}",
        )

        # -- A live CrewAI agent recalls the facts via the search tool. -------
        print("\n[Step 6] CrewAI agent recall via Zep search tool...")
        search_tool = create_search_tool(zep, user_id=USER_ID)
        agent = Agent(
            role="Personal Assistant",
            goal="Answer questions about the user using their Zep memory",
            backstory=(
                "You recall what you know about the user by searching Zep memory before answering."
            ),
            tools=[search_tool],
            llm=OPENAI_MODEL,
            verbose=False,
        )
        task = Task(
            description=(
                "Search Zep memory for the user's profile, then state in one sentence "
                "where they work, their role, and where they live."
            ),
            expected_output="One sentence summarizing the user's employer, role, and city.",
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        result = str(crew.kickoff()).lower()
        agent_found = [kw for kw in keywords if kw in result]
        print(f"  Agent recalled keywords: {agent_found}")
        passed &= check(
            "Agent recalled facts from memory",
            len(agent_found) > 0,
            f"found={agent_found}",
        )

    finally:
        print("\n[Cleanup] Deleting test user...")
        try:
            zep.user.delete(user_id=USER_ID)
            print(f"  Deleted {USER_ID}")
        except Exception as exc:
            print(f"  Warning: could not delete user: {exc}")

    print(f"\n{'=' * 70}")
    print("RESULT:", "ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED")
    print("=" * 70)
    sys.exit(0 if passed else 1)


@pytest.mark.integration
def test_integration_full_lifecycle() -> None:
    """Pytest entry point for the live integration test (synchronous)."""
    zep = Zep(api_key=ZEP_API_KEY)

    try:
        zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME, email=EMAIL)
        zep.thread.create(thread_id=THREAD_1, user_id=USER_ID)

        storage1 = ZepUserStorage(client=zep, user_id=USER_ID, thread_id=THREAD_1)
        seed_facts(storage1)

        user = zep.user.get(user_id=USER_ID)
        assert user.first_name == FIRST_NAME
        assert user.email == EMAIL

        t1 = zep.thread.get(thread_id=THREAD_1, lastn=20)
        messages = t1.messages or []
        assert any(m.role == "user" for m in messages)
        assert any(m.role == "assistant" for m in messages)

        wait_for_episodes_processed(zep, USER_ID, timeout_seconds=300)

        storage2 = ZepUserStorage(client=zep, user_id=USER_ID, thread_id=THREAD_2)
        results = storage2.search("What is IntegTest's job, location, and hobbies?", limit=10)
        keywords = ["acme", "data scientist", "portland", "hiking", "photography"]
        assert any(kw in str(results).lower() for kw in keywords), f"no recall in: {results}"

        search_tool = create_search_tool(zep, user_id=USER_ID)
        agent = Agent(
            role="Personal Assistant",
            goal="Answer questions about the user using their Zep memory",
            backstory="You recall what you know about the user by searching Zep memory.",
            tools=[search_tool],
            llm=OPENAI_MODEL,
            verbose=False,
        )
        task = Task(
            description=(
                "Search Zep memory for the user's profile, then state in one sentence "
                "where they work, their role, and where they live."
            ),
            expected_output="One sentence summarizing the user's employer, role, and city.",
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        result = str(crew.kickoff()).lower()
        assert any(kw in result for kw in keywords), f"agent did not recall: {result}"
    finally:
        try:
            zep.user.delete(user_id=USER_ID)
        except Exception:
            pass


if __name__ == "__main__":
    main()
