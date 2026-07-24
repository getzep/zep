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

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import DEFAULT_RISKY_WORDS, ingest_emails, search_when_ready

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_id = f"example-email-{int(time.time())}"

    result = ingest_emails(
        client,
        str(DATA / "emails" / "*.eml"),
        graph_id=graph_id,
        create_if_missing=True,
        ontology=ONTOLOGY,  # set BEFORE data flows — it is not retroactive
        # canonicalize retired code names, longhand phrasings, and casual
        # first-name references so each merges into one entity
        aliases={
            "ROBOT-202": ["PROTOTYPE-202", "ROBOT-202 program"],
            "Casey Nguyen": ["Casey Nguyen"],
            "Avery Brown": ["Avery Brown"],
        },
        risky_words=DEFAULT_RISKY_WORDS,  # reject aliases that are common words
        wait=True,
    )
    print(f"Submitted {result.items_submitted} emails via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Without wait=True you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "Who is responsible for the ROBOT-202 project?"
    response = search_when_ready(client, query, graph_id=graph_id, limit=5)
    print(f"\nSearch: {query}")
    for edge in response.edges or []:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_id}")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_id})")


if __name__ == "__main__":
    main()
