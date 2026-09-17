"""The examples ship bundled sample data — verify every file loads cleanly
through the real loaders (no API calls), so the zero-arg examples can't rot."""

import json
import sys
from pathlib import Path

from zep_ingest import EmlLoader, Pipeline, SlackExportLoader, TextChunker, TextFileLoader

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from example_ontology import EDGE_TYPES, ENTITY_TYPES  # noqa: E402
from fact_triples_example import load_triples  # noqa: E402
from thread_backfill_example import load_rows  # noqa: E402

DATA = Path(__file__).parent.parent / "examples" / "data"


def test_emails_load_with_real_dates():
    episodes = list(EmlLoader(str(DATA / "emails" / "*.eml")).load())
    assert len(episodes) == 3
    assert all(e.created_at is not None for e in episodes)
    assert all(e.data_type == "text" for e in episodes)


def test_slack_export_previews_clean():
    report = Pipeline(SlackExportLoader(DATA / "slack_export")).preview(limit=None)
    assert len(report.episodes) > 0
    assert all(e.created_at is not None for e in report.episodes)
    assert report.warnings == []


def test_handbook_produces_many_chunks():
    pipeline = Pipeline(
        TextFileLoader(str(DATA / "docs" / "company_handbook.md")),
        transforms=[TextChunker()],
    )
    report = pipeline.preview(limit=None)
    assert len(report.episodes) >= 4


def test_org_chart_molds_into_declared_edges():
    # the example molds a realistic directory export into FactTriples;
    # construction itself validates every documented limit
    triples = load_triples()
    assert len(triples) > 0
    declared_edges = {edge.name for edge in EDGE_TYPES}
    assert all(t.fact_name in declared_edges for t in triples)
    assert all(t.valid_at is not None for t in triples)
    molded = {t.fact_name for t in triples}
    assert molded <= declared_edges
    assert {"WORKS_AT", "RESPONSIBLE", "SUPPLIES", "CUSTOMER_OF", "LOCATED_AT"} <= molded
    # labels tie triple-created nodes to declared entity types
    entities = {entity.name for entity in ENTITY_TYPES}
    assert all((t.source_node_labels or ["x"])[0] in entities for t in triples)
    assert all((t.target_node_labels or ["x"])[0] in entities for t in triples)
    assert all(t.source_node_labels and t.target_node_labels for t in triples)


def test_thread_files_valid_and_multi_threaded():
    # The bundled exports carry their own thread names and timestamps; the
    # examples map each name onto the UUID of a thread they create, because
    # v4 addresses a thread by UUID.
    for name in ("chat_history.jsonl", "combined_threads.jsonl"):
        rows = load_rows(DATA / name)
        assert rows, name
        assert all(row["created_at"] for row in rows), name
        assert all(row["role"] and row["name"] and row["content"] for row in rows), name
        assert len({row["thread_id"] for row in rows}) >= 2, name


def test_products_have_mapped_fields():
    records = json.loads((DATA / "products.json").read_text())
    assert len(records) > 0
    for record in records:
        assert {"sku", "title", "about", "updated_at"} <= set(record)
