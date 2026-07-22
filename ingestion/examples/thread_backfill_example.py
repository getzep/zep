"""Backfill a user's chat history into their user graph via threads.

Business data goes to named graphs; a user's own conversations go to their
user graph as thread messages — that's what powers thread.get_user_context().

Self-contained and re-runnable: creates a fresh user, sets the starter
ontology on their graph, and ingests the bundled examples/data/
chat_history.jsonl. Every message is validated client-side (role, RFC3339
created_at, metadata limits) before the first API call, oversize messages
are split at sentence boundaries, and per-thread order is preserved.

Usage:
    export ZEP_API_KEY=...
    python thread_backfill_example.py

chat_history.jsonl rows (one message per line, chronological order; a JSON
array with the same columns also works):
    {"thread_id": "support-1001", "role": "user", "name": "Morgan Lee",
     "content": "Half my team can't log into OPERATIONS-DASHBOARD...",
     "created_at": "2025-04-10T15:02:00Z"}
    {"thread_id": "support-1001", "role": "assistant", "name": "Riley Chen",
     "content": "Are the affected users seeing an error from OPERATIONS-DASHBOARD...",
     "created_at": "2025-04-10T15:03:00Z"}
"""

import time
from pathlib import Path

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import ingest_thread_messages, search_when_ready

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    run_id = int(time.time())
    user_id = f"example-backfill-{run_id}"

    client.user.add(user_id=user_id, first_name="Morgan", last_name="Example")

    # On user graphs custom types are ADDITIVE to Zep's defaults (User,
    # Preference, Location, ...); set them before the backfill flows.
    client.graph.set_ontology(
        entities=ONTOLOGY["entities"],
        edges=ONTOLOGY["edges"],
        user_ids=[user_id],
    )

    result = ingest_thread_messages(
        client,
        DATA / "chat_history.jsonl",
        user_id=user_id,  # any missing threads are created for you
        # Thread ids are global to a Zep project; the suffix keeps re-runs of
        # this example from appending to an earlier run's threads. Skip it
        # when your ids are already unique.
        thread_id_suffix=f"-{run_id}",
    )
    print(f"Submitted {result.items_submitted} messages via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.add_errors:
        print(f"ERROR: {error}")

    # Extraction is asynchronous; wait until facts are searchable, then pull
    # the context block an agent would receive for one of the threads.
    search_when_ready(client, "OPERATIONS-DASHBOARD", user_id=user_id)
    context = client.thread.get_user_context(thread_id=f"support-1001-{run_id}")
    print(f"\nUser context for thread support-1001-{run_id}:")
    print(context.context)

    print(f"\nUser: {user_id}")
    print(f"Explore the graph at https://app.getzep.com (Users -> {user_id} -> Graph)")


if __name__ == "__main__":
    main()
