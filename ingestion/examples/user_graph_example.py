"""Build one user's graph from combined sources: profile facts, chat threads,
then a document — the shape most real deployments take.

Everything lands on a single USER graph: seed what you already know about the
user as fact triples, backfill their conversations as thread messages, then
add related documents. Ordering matters twice: the ontology is set before any
data flows, and the profile facts land before the narrative so extraction
resolves against known entities.

v4 addresses a user, a thread, and a graph by a server-generated UUID. The
example creates each one and reads the UUID from the response; zep-ingest
creates no user or thread and does no runtime lookup.

Usage:
    export ZEP_API_KEY=...
    python user_graph_example.py
"""

import json
from pathlib import Path

from example_ontology import EDGE_TYPES, ENTITY_TYPES
from zep_cloud.client import Zep

from zep_ingest import (
    FactTriple,
    ThreadMessage,
    ingest_documents,
    ingest_fact_triples,
    ingest_thread_messages,
    search_when_ready,
)

DATA = Path(__file__).parent / "data"


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_messages(client: Zep, rows: list[dict], user_uuid: str) -> list[ThreadMessage]:
    """Create one thread for each conversation and map its rows onto it.

    v4 has no reference-time field for a thread message, so the export's
    created_at is kept in metadata.
    """
    thread_uuids: dict[str, str] = {}
    messages: list[ThreadMessage] = []
    for row in rows:
        name = row["thread_id"]
        if name not in thread_uuids:
            thread = client.thread.create(user_uuid=user_uuid)
            thread_uuids[name] = thread.uuid_
        messages.append(
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

    # 1. Create the user (the user graph comes with it) and read the UUIDs
    #    from the response.
    user = client.user.create(
        first_name="Morgan",
        last_name="Example",
        email="morgan@clearwater-fulfillment.example",
    )
    user_uuid = user.uuid_
    user_graph_uuid = user.graph_uuid

    # 2. Set the ontology BEFORE any data flows. On user graphs custom types
    #    are ADDITIVE to Zep's defaults (User, Preference, Location, ...).
    client.graph.set_ontology(user_graph_uuid, entity_types=ENTITY_TYPES, edge_types=EDGE_TYPES)

    # 3. Seed what you already know about the user as explicit fact triples —
    #    later extraction resolves against these known entities.
    profile = [
        FactTriple(
            fact="Morgan Lee is an Operations Manager at Clearwater Fulfillment",
            fact_name="WORKS_AT",
            source_node_name="Morgan Lee",
            source_node_labels=["Person"],  # ties the node to the declared type
            source_node_summary="Operations Manager running the ROBOT-101 pilot",
            target_node_name="Clearwater Fulfillment",
            target_node_labels=["Organization"],
            target_node_summary="Regional fulfillment provider piloting Alder Ridge Robotics arms",
            valid_at="2025-03-01T00:00:00Z",
        ),
        FactTriple(
            fact="Morgan Lee is based at the South Warehouse fulfillment center",
            fact_name="LOCATED_AT",
            source_node_name="Morgan Lee",
            source_node_labels=["Person"],
            target_node_name="South Warehouse",
            target_node_labels=["Location"],
            target_node_summary="Clearwater Fulfillment warehouse location",
            valid_at="2025-03-01T00:00:00Z",
        ),
        FactTriple(
            fact="Clearwater Fulfillment operates the South Warehouse pilot",
            fact_name="LOCATED_AT",
            source_node_name="Clearwater Fulfillment",
            source_node_labels=["Organization"],
            target_node_name="South Warehouse",
            target_node_labels=["Location"],
            valid_at="2025-03-01T00:00:00Z",
        ),
    ]
    profile_result = ingest_fact_triples(client, profile, graph_uuid=user_graph_uuid)
    profile_result.wait()
    profile_result.raise_for_status()
    print(f"Seeded {len(profile)} profile facts")

    # 4. Backfill the user's support conversations as thread messages. The
    #    example creates each thread and keeps its UUID; per-thread order is
    #    preserved.
    messages = to_messages(client, load_rows(DATA / "combined_threads.jsonl"), user_uuid)
    threads = ingest_thread_messages(client, messages)
    print(f"Backfilled {threads.items_submitted} thread messages: {threads.status}")

    # 5. Documents can land on a user graph too — Morgan Lee's deployment notes.
    docs = ingest_documents(
        client,
        str(DATA / "deployment_notes.md"),
        graph_uuid=user_graph_uuid,
        created_at="2025-06-20T00:00:00Z",  # generated source date
    )
    docs.wait()
    print(f"Ingested {docs.items_submitted} document chunks: {docs.status}")

    # Extraction is asynchronous; wait until facts are searchable, then pull
    # the context block an agent would receive for the user.
    search_when_ready(client, "Arm 3 calibration", graph_uuid=user_graph_uuid)
    context = client.graph.get_context(user_graph_uuid, query="Arm 3 calibration")
    print(f"\nUser context for graph {user_graph_uuid}:")
    print(context.context)

    print(f"\nUser: {user_uuid}")
    print("Explore the graph at https://app.getzep.com (Users -> Graph)")


if __name__ == "__main__":
    main()
