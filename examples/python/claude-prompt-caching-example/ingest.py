"""Seed the demo user's Zep graph with prior conversations.

Creates the fixed demo user (``scenario.DEMO_USER_ID``), ingests two prior
conversations into two threads, and polls until Zep has finished extracting
entities and facts from every episode. Run this once before `chat.py` or
`benchmark.py` — both use the same user ID, so the agent starts with real
cross-session memory.

Usage:

    python ingest.py              # create + seed the demo user
    python ingest.py --recreate   # delete the demo user first, then re-seed
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.types import Message

import scenario
from agent import wait_for_zep_processing


def user_exists(zep: Zep, user_id: str) -> bool:
    try:
        zep.user.get(user_id=user_id)
        return True
    except ApiError as e:
        if e.status_code == 404:
            return False
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo user's Zep graph with prior conversations.")
    parser.add_argument("--recreate", action="store_true", help="Delete the demo user first, then re-seed from scratch.")
    args = parser.parse_args()

    load_dotenv()
    zep_key = os.getenv("ZEP_API_KEY")
    if not zep_key:
        sys.exit("Set ZEP_API_KEY in .env first (see .env.example).")
    zep = Zep(api_key=zep_key)

    user_id = scenario.DEMO_USER_ID
    if user_exists(zep, user_id):
        if not args.recreate:
            sys.exit(
                f"User '{user_id}' already exists — it looks like ingestion has already run.\n"
                "Re-running would duplicate the seed conversations in the graph.\n"
                "Use --recreate to delete the user and re-seed from scratch."
            )
        print(f"Deleting existing user {user_id}...")
        zep.user.delete(user_id=user_id)
        time.sleep(2)

    zep.user.add(user_id=user_id, first_name="Dana", last_name="Patel")
    print(f"Created user {user_id}")

    for i, conversation in enumerate(scenario.PRIOR_CONVERSATIONS, start=1):
        thread_id = f"{user_id}-prior-{i}"
        zep.thread.create(thread_id=thread_id, user_id=user_id)
        zep.thread.add_messages(
            thread_id=thread_id,
            messages=[
                Message(role=m["role"], name=m.get("name"), content=m["content"]) for m in conversation
            ],
        )
        print(f"Ingested prior conversation {i} ({len(conversation)} messages) into thread {thread_id}")

    print("Waiting for Zep to finish extracting entities and facts...")
    ok = wait_for_zep_processing(zep, user_id, timeout_s=900.0)
    if ok:
        print(f"Done — user '{user_id}' is seeded and fully processed.")
        print("Next: python chat.py   or   python benchmark.py --conversation short")
    else:
        sys.exit("Timed out waiting for Zep processing — check the project dashboard and retry.")


if __name__ == "__main__":
    main()
