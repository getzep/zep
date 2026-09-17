"""Ingest text/Markdown documents with chunking + optional LLM contextualization.

Self-contained and re-runnable: creates a fresh graph, sets the starter
ontology, and ingests the bundled company handbook under examples/data/docs/,
split into ~500-character chunks. If an LLM key is present, each chunk is
contextualized within its document before ingestion — richer graphs than
naive chunking; without one, plain chunking still works well.

Usage:
    export ZEP_API_KEY=...
    export ANTHROPIC_API_KEY=...   # optional (or OPENAI_API_KEY)
    python documents_example.py
"""

import os
import time
from pathlib import Path

from example_ontology import EDGE_TYPES, ENTITY_TYPES
from zep_cloud.client import Zep

from zep_ingest import ZepDependencyError, ingest_documents, search_when_ready
from zep_ingest.protocols import LLMClient

DATA = Path(__file__).parent / "data"


def pick_llm() -> LLMClient | None:
    try:
        if os.getenv("ANTHROPIC_API_KEY"):
            from zep_ingest.llm.anthropic import AnthropicLLM

            return AnthropicLLM()
        if os.getenv("OPENAI_API_KEY"):
            from zep_ingest.llm.openai import OpenAILLM

            return OpenAILLM()
    except ZepDependencyError as exc:
        print(f"LLM contextualization skipped: {exc}")
    return None


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_name = f"example-docs-{int(time.time())}"

    llm = pick_llm()
    if llm is None:
        print("No LLM key found — chunking without contextualization (works fine).")

    # Create the graph and set its ontology up front: ingestion writes only into
    # existing graphs, and the ontology is not retroactive.
    # v4 addresses a graph by the UUID that graph.create returns; graph_name
    # is only a label.
    graph = client.graph.create(name=graph_name)
    graph_uuid = graph.uuid_
    client.graph.set_ontology(graph_uuid, entity_types=ENTITY_TYPES, edge_types=EDGE_TYPES)
    result = ingest_documents(
        client,
        str(DATA / "docs" / "company_handbook.md"),
        graph_uuid=graph_uuid,
        llm=llm,
        created_at="2025-06-01T00:00:00Z",  # generated source date
        # Sequential keeps created_at; a v4 batch item has no
        # reference-time field.
        method="sequential",
    )
    # Submission returns immediately; blocking is opt-in. Bind the result first
    # so a wait() timeout still leaves you the ids below to resume from.
    result.wait()
    print(f"Submitted {result.items_submitted} chunks via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Instead of waiting you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "What products does Alder Ridge Robotics sell?"
    edges = search_when_ready(client, query, graph_uuid=graph_uuid, limit=5)
    print(f"\nSearch: {query}")
    for edge in edges:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_name} ({graph_uuid})")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_name})")


if __name__ == "__main__":
    main()
