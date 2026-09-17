"""Ingest exported emails (.eml files) into a Zep graph.

Self-contained and re-runnable: creates a fresh graph, sets the starter
ontology, and ingests the bundled sample emails under examples/data/emails/.
Each email's Date header becomes the episode timestamp, so backfilled
correspondence keeps its real timeline. Point the glob at your own export
(e.g. "exports/**/*.eml") to ingest real mail.

Usage:
    export ZEP_API_KEY=...
    python email_example.py
"""

import time
from pathlib import Path

from example_ontology import EDGE_TYPES, ENTITY_TYPES
from zep_cloud.client import Zep

from zep_ingest import DEFAULT_RISKY_WORDS, ingest_emails, search_when_ready

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_name = f"example-email-{int(time.time())}"

    # Create the graph and set its ontology up front: ingestion writes only into
    # existing graphs, and the ontology is not retroactive.
    # v4 addresses a graph by the UUID that graph.create returns; graph_name
    # is only a label.
    graph = client.graph.create(name=graph_name)
    graph_uuid = graph.uuid_
    client.graph.set_ontology(graph_uuid, entity_types=ENTITY_TYPES, edge_types=EDGE_TYPES)
    result = ingest_emails(
        client,
        str(DATA / "emails" / "*.eml"),
        graph_uuid=graph_uuid,
        # canonicalize retired code names, longhand phrasings, and casual
        # first-name references so each merges into one entity
        aliases={
            "ROBOT-202": ["PROTOTYPE-202", "ROBOT-202 program"],
            "Casey Nguyen": ["Casey Nguyen"],
            "Avery Brown": ["Avery Brown"],
        },
        risky_words=DEFAULT_RISKY_WORDS,  # reject aliases that are common words
        # Sequential keeps each email's Date: header as the reference time;
        # a v4 batch item has no such field.
        method="sequential",
    )
    # Submission returns immediately; blocking is opt-in. Bind the result first
    # so a wait() timeout still leaves you the ids below to resume from.
    result.wait()
    print(f"Submitted {result.items_submitted} emails via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Instead of waiting you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "Who is responsible for the ROBOT-202 project?"
    edges = search_when_ready(client, query, graph_uuid=graph_uuid, limit=5)
    print(f"\nSearch: {query}")
    for edge in edges:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_name} ({graph_uuid})")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_name})")


if __name__ == "__main__":
    main()
