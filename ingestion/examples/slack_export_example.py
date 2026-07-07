"""Ingest a Slack workspace export into a Zep graph.

Self-contained and re-runnable: previews then ingests the bundled sample
export under examples/data/slack_export/, creating a fresh graph and setting
the starter ontology first. Messages are grouped by thread, join/leave noise
is skipped, and each episode keeps its real Slack timestamp.

To run against real data, get an export from Slack (Workspace Admin →
Settings & administration → Workspace settings → Import/Export Data →
Export) and point the loader at the .zip or unpacked directory.

Usage:
    export ZEP_API_KEY=...
    python slack_export_example.py
"""

import time
from pathlib import Path

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import (
    DEFAULT_RISKY_WORDS,
    Pipeline,
    SlackExportLoader,
    ingest_slack_export,
    search_when_ready,
)

DATA = Path(__file__).parent / "data"


def main() -> None:
    # Preview costs nothing: no API calls, just what WOULD be ingested plus
    # warnings (missing timestamps, oversize splits) before spending quota.
    report = Pipeline(SlackExportLoader(DATA / "slack_export")).preview(limit=3)
    print("Preview (first 3 episodes):")
    for episode in report.episodes:
        print(f"--- {episode.created_at}\n{episode.data[:300]}\n")
    for warning in report.warnings:
        print(f"WARNING: {warning}")

    client = Zep()  # reads ZEP_API_KEY
    graph_id = f"example-slack-{int(time.time())}"

    result = ingest_slack_export(
        client,
        DATA / "slack_export",
        graph_id=graph_id,
        create_if_missing=True,
        ontology=ONTOLOGY,  # set BEFORE data flows — it is not retroactive
        aliases={
            "Atlas": ["MR-42", "Atlas program"],  # retired code name + longhand
            "Voltaic Components": ["Voltaic"],  # casual shorthand in chat
        },
        # Guard the alias map (opt-in): rewriting a common word like "will"
        # corrupts unrelated text corpus-wide. Extend with your own people's
        # word-like names: DEFAULT_RISKY_WORDS | {"chip", "sunny"}
        risky_words=DEFAULT_RISKY_WORDS,
        # skip_subtypes=frozenset(),  # keep join/leave/topic messages
        # skip_subtypes=DEFAULT_SKIP_SUBTYPES | {"huddle_thread"},  # skip more
        #   (import DEFAULT_SKIP_SUBTYPES from zep_ingest to extend the default)
        # include_bots=True,  # keep bot messages
        wait=True,
    )
    print(f"Submitted {result.items_submitted} episodes via {result.method}: {result.status}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.add_errors:
        print(f"ERROR: {error}")
    if result.batch_ids:
        # Without wait=True you can persist these ids and check later:
        #   IngestResult.from_batch_ids(client, batch_ids).status
        print(f"Batch ids: {result.batch_ids}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "What is the open risk on the Atlas project?"
    response = search_when_ready(client, query, graph_id=graph_id, limit=5)
    print(f"\nSearch: {query}")
    for edge in response.edges or []:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_id}")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_id})")


if __name__ == "__main__":
    main()
