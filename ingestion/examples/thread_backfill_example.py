"""Backfill a user's chat history into their user graph via threads.

Business data goes to named graphs; a user's own conversations go to their
user graph as thread messages — that's what powers the user context block.

Self-contained and re-runnable: creates a fresh user, sets the starter
ontology on their graph, creates one thread for each conversation in the
bundled examples/data/chat_history.jsonl, and backfills the messages. Every
message is validated client-side (thread_uuid, role, metadata limits) before
the first API call, oversize messages are split at sentence boundaries, and
per-thread order is preserved.

v4 addresses a user and a thread by a server-generated UUID. The application
creates each one time, keeps the UUID, and passes it to zep-ingest, which
creates no user or thread and does no runtime lookup. The example keeps the
export's own thread names only as a local grouping key.

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

import json
from pathlib import Path

from example_ontology import EDGE_TYPES, ENTITY_TYPES
from zep_cloud.client import Zep

from zep_ingest import ThreadMessage, ingest_thread_messages, search_when_ready

DATA = Path(__file__).parent / "data"


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_messages(client: Zep, rows: list[dict], user_uuid: str) -> dict[str, list[ThreadMessage]]:
    """Create one thread for each conversation and map its rows onto it.

    v4 has no reference-time field for a thread message, so the export's
    created_at is kept in metadata.
    """
    thread_uuids: dict[str, str] = {}
    messages: dict[str, list[ThreadMessage]] = {}
    for row in rows:
        name = row["thread_id"]
        if name not in thread_uuids:
            thread = client.thread.create(user_uuid=user_uuid)
            thread_uuids[name] = thread.uuid_
            messages[name] = []
        messages[name].append(
            ThreadMessage(
                thread_uuid=thread_uuids[name],
                role=row["role"],
                name=row["name"],
                content=row["content"],
                metadata={"source_created_at": row["created_at"]},
            )
        )
    return messages


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY

    # 1. Create the user and read the UUIDs from the response — a create call
    #    does not supply an identifier of its own in v4.
    user = client.user.create(first_name="Morgan", last_name="Example")
    user_uuid = user.uuid_
    user_graph_uuid = user.graph_uuid

    # On user graphs custom types are ADDITIVE to Zep's defaults (User,
    # Preference, Location, ...); set them before the backfill flows.
    client.graph.set_ontology(user_graph_uuid, entity_types=ENTITY_TYPES, edge_types=EDGE_TYPES)

    # 2. Create one thread for each conversation, then backfill the messages.
    by_thread = to_messages(client, load_rows(DATA / "chat_history.jsonl"), user_uuid)
    flat = [message for thread_messages in by_thread.values() for message in thread_messages]
    result = ingest_thread_messages(
        client,
        flat,
        # Keep assistant turns as context but exclude them from graph
        # extraction (they still land in thread history):
        # ignore_roles=["assistant"],
    )
    print(f"Submitted {result.items_submitted} messages via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.add_errors:
        print(f"ERROR: {error}")

    # Extraction is asynchronous; wait until facts are searchable, then pull
    # the context block an agent would receive for the user.
    search_when_ready(client, "OPERATIONS-DASHBOARD", graph_uuid=user_graph_uuid)
    context = client.graph.get_context(user_graph_uuid, query="OPERATIONS-DASHBOARD")
    print(f"\nUser context for graph {user_graph_uuid}:")
    print(context.context)

    print(f"\nUser: {user_uuid}")
    print("Explore the graph at https://app.getzep.com (Users -> Graph)")


if __name__ == "__main__":
    main()
