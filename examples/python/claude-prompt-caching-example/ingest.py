"""Create a demo user and seed its graph with prior conversations.

The script prints the user UUID. Set ``ZEP_USER_UUID`` to this value before
running ``chat.py`` or ``benchmark.py``.
"""

from __future__ import annotations

import os
import sys
import uuid

from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import Message

import scenario
from agent import wait_for_zep_processing


def main() -> None:
    load_dotenv()
    zep_key = os.getenv("ZEP_API_KEY")
    if not zep_key:
        sys.exit("Set ZEP_API_KEY in .env first (see .env.example).")
    zep = Zep(api_key=zep_key)

    user = zep.user.create(
        user_id=f"{scenario.DEMO_USER_ID}-{uuid.uuid4().hex[:8]}",
        first_name="Dana",
        last_name="Patel",
    )
    user_uuid = user.uuid_
    print(f"Created user. Set ZEP_USER_UUID={user_uuid} for chat.py and benchmark.py.")

    for i, conversation in enumerate(scenario.PRIOR_CONVERSATIONS, start=1):
        thread = zep.thread.create(user_uuid=user_uuid)
        zep.thread.add_messages(
            thread_uuid=thread.uuid_,
            messages=[
                Message(role=m["role"], name=m.get("name"), content=m["content"]) for m in conversation
            ],
        )
        print(f"Ingested prior conversation {i} ({len(conversation)} messages) into thread {thread.uuid_}")

    print("Waiting for Zep to finish extracting entities and facts...")
    ok = wait_for_zep_processing(zep, user_uuid, timeout_s=900.0)
    if ok:
        print(f"Done — user '{user_uuid}' is seeded and fully processed.")
        print("Next: python chat.py   or   python benchmark.py --conversation short")
    else:
        sys.exit("Timed out waiting for Zep processing — check the project dashboard and retry.")


if __name__ == "__main__":
    main()
