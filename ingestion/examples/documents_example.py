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

from example_ontology import ONTOLOGY
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
    graph_id = f"example-docs-{int(time.time())}"

    llm = pick_llm()
    if llm is None:
        print("No LLM key found — chunking without contextualization (works fine).")

    result = ingest_documents(
        client,
        str(DATA / "docs" / "meridian_company_handbook.md"),
        graph_id=graph_id,
        create_if_missing=True,
        ontology=ONTOLOGY,  # set BEFORE data flows — it is not retroactive
        llm=llm,
        created_at="2025-06-01T00:00:00Z",  # date the corpus; omit for file mtime
        wait=True,
    )
    print(f"Submitted {result.items_submitted} chunks via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if result.batch_ids:
        # Without wait=True you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "What products does Meridian Robotics sell?"
    response = search_when_ready(client, query, graph_id=graph_id, limit=5)
    print(f"\nSearch: {query}")
    for edge in response.edges or []:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_id}")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_id})")


if __name__ == "__main__":
    main()
