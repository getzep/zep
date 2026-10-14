"""Seed the demo user's Zep graph with prior conversations.

Creates the demo user, stores the UUID of the user in
``scenario.DEMO_USER_UUID_FILE``, ingests two prior conversations into two
threads, and polls until Zep has finished extracting entities and facts from
every episode. Run this once before `chat.py` or `benchmark.py` — both read
the same UUID, so the agent starts with real cross-session memory.

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
from zep_cloud import AddMessage
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

import scenario
from agent import wait_for_zep_processing


def user_exists(zep: Zep, user_uuid: str) -> bool:
    try:
        zep.user.get(user_uuid)
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

    existing_uuid = scenario.load_demo_user_uuid()
    if existing_uuid and user_exists(zep, existing_uuid):
        if not args.recreate:
            sys.exit(
                f"User '{existing_uuid}' already exists — it looks like ingestion has already run.\n"
                "Re-running would duplicate the seed conversations in the graph.\n"
                "Use --recreate to delete the user and re-seed from scratch."
            )
        print(f"Deleting existing user {existing_uuid}...")
        zep.user.delete(existing_uuid)
        time.sleep(2)

    user = zep.user.create(first_name="Dana", last_name="Patel")
    if user.uuid_ is None or user.graph_uuid is None:
        sys.exit("Zep did not return a user UUID and a graph UUID.")
    scenario.save_demo_user_uuid(user.uuid_)
    print(f"Created user {user.uuid_}")

    for i, conversation in enumerate(scenario.PRIOR_CONVERSATIONS, start=1):
        thread = zep.thread.create(user_uuid=user.uuid_)
        zep.thread.add_messages(
            thread.uuid_,
            messages=[
                AddMessage(role=m["role"], name=m.get("name"), content=m["content"]) for m in conversation
            ],
        )
        print(f"Ingested prior conversation {i} ({len(conversation)} messages) into thread {thread.uuid_}")

    print("Waiting for Zep to finish extracting entities and facts...")
    ok = wait_for_zep_processing(zep, user.graph_uuid, timeout_s=900.0)
    if ok:
        print(f"Done — user '{user.uuid_}' is seeded and fully processed.")
        print("Next: python chat.py   or   python benchmark.py --conversation short")
    else:
        sys.exit("Timed out waiting for Zep processing — check the project dashboard and retry.")


if __name__ == "__main__":
    main()
