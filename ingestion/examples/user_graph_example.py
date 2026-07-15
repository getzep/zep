"""Build one user's graph from combined sources: profile facts, chat threads,
then a document — the shape most real deployments take.

Everything lands on a single USER graph (no graph_id anywhere): seed what you
already know about the user as fact triples, backfill their conversations as
thread messages, then add related documents. Ordering matters twice: the
ontology is set before any data flows, and the profile facts land before the
narrative so extraction resolves against known entities.

Usage:
    export ZEP_API_KEY=...
    python user_graph_example.py
"""

import time
from pathlib import Path

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import (
    FactTriple,
    ingest_documents,
    ingest_fact_triples,
    ingest_thread_messages,
    search_when_ready,
)

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    run_id = int(time.time())
    user_id = f"example-user-{run_id}"

    # 1. Create the user (the user graph comes with it).
    client.user.add(
        user_id=user_id,
        first_name="Morgan",
        last_name="Example",
        email="morgan@clearwater-fulfillment.example",
    )

    # 2. Set the ontology BEFORE any data flows. On user graphs custom types
    #    are ADDITIVE to Zep's defaults (User, Preference, Location, ...).
    client.graph.set_ontology(
        entities=ONTOLOGY["entities"],
        edges=ONTOLOGY["edges"],
        user_ids=[user_id],
    )

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
    profile_result = ingest_fact_triples(client, profile, user_id=user_id)
    profile_result.wait(timeout=600)
    profile_result.raise_for_status()
    print(f"Seeded {len(profile)} profile facts")

    # 4. Backfill the user's support conversations as thread messages —
    #    threads are created for you, per-thread order preserved. Thread ids
    #    are global to a Zep project; the suffix keeps re-runs from appending
    #    to an earlier run's threads.
    threads = ingest_thread_messages(
        client,
        DATA / "combined_threads.jsonl",
        user_id=user_id,
        thread_id_suffix=f"-{run_id}",
    )
    print(f"Backfilled {threads.items_submitted} thread messages: {threads.status}")

    # 5. Documents can land on a user graph too — Morgan Lee's deployment notes.
    docs = ingest_documents(
        client,
        str(DATA / "deployment_notes.md"),
        user_id=user_id,
        created_at="2025-06-20T00:00:00Z",  # generated source date
        wait=True,
    )
    print(f"Ingested {docs.items_submitted} document chunks: {docs.status}")

    # Extraction is asynchronous; wait until facts are searchable, then pull
    # the context block an agent would receive for one of the threads.
    search_when_ready(client, "Arm 3 calibration", user_id=user_id)
    context = client.thread.get_user_context(thread_id=f"support-1-{run_id}")
    print(f"\nUser context for thread support-1-{run_id}:")
    print(context.context)

    print(f"\nUser: {user_id}")
    print(f"Explore the graph at https://app.getzep.com (Users -> {user_id} -> Graph)")


if __name__ == "__main__":
    main()
