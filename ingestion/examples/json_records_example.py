"""Ingest structured records (JSON/JSONL/CSV) as normalized json episodes.

Self-contained and re-runnable: creates a fresh graph, sets the starter
ontology, and ingests the bundled product catalog under examples/data/.
Zep EXTRACTS entities and relationships from the record contents — contrast
with fact_triples_example.py, where you state known relationships exactly.

The field mapping (id_field, name_field, ...) tells the loader which columns
identify and describe each record; created_at_field keeps real timestamps so
backfilled records don't all land "now".

Usage:
    export ZEP_API_KEY=...
    python json_records_example.py
"""

import time
from pathlib import Path

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import ingest_json_records, search_when_ready

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_id = f"example-records-{int(time.time())}"

    result = ingest_json_records(
        client,
        str(DATA / "products.json"),
        graph_id=graph_id,
        create_if_missing=True,
        ontology=ONTOLOGY,  # set BEFORE data flows — it is not retroactive
        id_field="sku",
        name_field="title",
        description_field="about",
        created_at_field="updated_at",
        metadata_fields=("category",),
        record_type="product",
        wait=True,
    )
    print(f"Submitted {result.items_submitted} records via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Without wait=True you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "Which company supplies parts for ROBOT-101?"
    response = search_when_ready(client, query, graph_id=graph_id, limit=5)
    print(f"\nSearch: {query}")
    for edge in response.edges or []:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_id}")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_id})")


if __name__ == "__main__":
    main()
