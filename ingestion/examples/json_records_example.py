"""Ingest structured records (JSON/JSONL/CSV) as one json episode per record.

Self-contained and re-runnable: creates a fresh graph, sets the starter
ontology, and ingests the bundled product catalog under examples/data/.
Zep EXTRACTS entities and relationships from the record contents — contrast
with fact_triples_example.py, where you state known relationships exactly.

Records are ingested exactly as provided, so shaping them for good extraction
is your job — the bundled catalog is flat and describes one product per record.
The field mapping (id_field, name_field, ...) tells the loader which columns
identify and describe each record; created_at_field keeps real timestamps so
backfilled records don't all land "now".

Usage:
    export ZEP_API_KEY=...
    python json_records_example.py
"""

import time
from pathlib import Path

from example_ontology import EDGE_TYPES, ENTITY_TYPES
from zep_cloud.client import Zep

from zep_ingest import ingest_json_records, search_when_ready

DATA = Path(__file__).parent / "data"


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_name = f"example-records-{int(time.time())}"

    # Create the graph and set its ontology up front: ingestion writes only into
    # existing graphs, and the ontology is not retroactive.
    # v4 addresses a graph by the UUID that graph.create returns; graph_name
    # is only a label.
    graph = client.graph.create(name=graph_name)
    graph_uuid = graph.uuid_
    client.graph.set_ontology(graph_uuid, entity_types=ENTITY_TYPES, edge_types=EDGE_TYPES)
    result = ingest_json_records(
        client,
        str(DATA / "products.json"),
        graph_uuid=graph_uuid,
        id_field="sku",
        name_field="title",
        description_field="about",
        created_at_field="updated_at",
        metadata_fields=("category",),
        record_type="product",
        # Sequential keeps created_at; a v4 batch item has no
        # reference-time field.
        method="sequential",
    )
    # Submission returns immediately; blocking is opt-in. Bind the result first
    # so a wait() timeout still leaves you the ids below to resume from.
    result.wait()
    print(f"Submitted {result.items_submitted} records via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Instead of waiting you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "Which company supplies parts for ROBOT-101?"
    edges = search_when_ready(client, query, graph_uuid=graph_uuid, limit=5)
    print(f"\nSearch: {query}")
    for edge in edges:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_name} ({graph_uuid})")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_name})")


if __name__ == "__main__":
    main()
