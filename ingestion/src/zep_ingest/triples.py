"""Fact triples — the explicit ingestion path for known, exact relationships.

Where LLM extraction is the wrong tool (product catalogs, org charts, seeding
canonical entities before a corpus ingest), assert facts directly. Every
documented API limit is validated client-side at construction, so a bad triple
is a clear Python error naming the field — not an HTTP 400 mid-run.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zep_cloud.client import Zep

from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest._validation import check_scalar_map, check_timestamp
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.types import MAX_METADATA_KEYS, Destination

MAX_FACT_CHARS = 250
MAX_NODE_NAME_CHARS = 50
MAX_FACT_NAME_CHARS = 50
MAX_SUMMARY_CHARS = 500

_FACT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _check_len(field: str, value: str | None, limit: int, errors: list[str]) -> None:
    if value is not None and len(value) > limit:
        errors.append(f"{field} exceeds {limit} characters (got {len(value)})")


@dataclass(slots=True)
class FactTriple:
    """One fact edge between two named nodes, validated against the API limits."""

    fact: str
    fact_name: str
    source_node_name: str
    target_node_name: str
    source_node_summary: str | None = None
    target_node_summary: str | None = None
    source_node_labels: list[str] | None = None
    target_node_labels: list[str] | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    created_at: str | None = None
    attributes: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        _check_len("fact", self.fact, MAX_FACT_CHARS, errors)
        _check_len("fact_name", self.fact_name, MAX_FACT_NAME_CHARS, errors)
        if not _FACT_NAME.match(self.fact_name):
            errors.append(
                f"fact_name must be SCREAMING_SNAKE_CASE (e.g. WORKS_AT): {self.fact_name!r}"
            )
        _check_len("source_node_name", self.source_node_name, MAX_NODE_NAME_CHARS, errors)
        _check_len("target_node_name", self.target_node_name, MAX_NODE_NAME_CHARS, errors)
        _check_len("source_node_summary", self.source_node_summary, MAX_SUMMARY_CHARS, errors)
        _check_len("target_node_summary", self.target_node_summary, MAX_SUMMARY_CHARS, errors)
        for field in ("source_node_labels", "target_node_labels"):
            labels = getattr(self, field)
            if labels is None:
                continue
            if not isinstance(labels, list):
                errors.append(
                    f"{field} must be a list with one entity-type label, got "
                    f"{type(labels).__name__} (note: CSV columns cannot express "
                    "lists — use JSONL or a JSON array)"
                )
            elif len(labels) > 1:
                errors.append(
                    f"{field} allows at most one entity-type label (extraction assigns "
                    f"one best-match type per node); got {labels!r}"
                )
        check_timestamp("valid_at", self.valid_at, errors)
        check_timestamp("invalid_at", self.invalid_at, errors)
        check_timestamp("created_at", self.created_at, errors)
        check_scalar_map("attributes", self.attributes, errors)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_METADATA_KEYS)
        if errors:
            raise ConfigurationError(
                f"Invalid fact triple ({self.fact[:60]!r}): " + "; ".join(errors)
            )

    def to_api_kwargs(self, destination: Destination) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "fact": self.fact,
            "fact_name": self.fact_name,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
        }
        for field in (
            "source_node_summary",
            "target_node_summary",
            "source_node_labels",
            "target_node_labels",
            "valid_at",
            "invalid_at",
            "created_at",
            "metadata",
        ):
            value = getattr(self, field)
            if value is not None:
                kwargs[field] = value
        if self.attributes is not None:
            kwargs["edge_attributes"] = self.attributes
        if destination.graph_id is not None:
            kwargs["graph_id"] = destination.graph_id
        else:
            kwargs["user_id"] = destination.user_id
        return kwargs


_TRIPLE_FIELDS = frozenset(FactTriple.__dataclass_fields__)


def _load_triples(path: Path) -> list[FactTriple]:
    rows = rows_to_fields(load_rows(path), _TRIPLE_FIELDS)
    return [FactTriple(**row) for row in rows]


def ingest_fact_triples(
    client: Zep,
    triples: Iterable[FactTriple] | str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    max_retries: int = 5,
) -> IngestResult:
    """Submit fact triples via graph.add_fact_triple (sequential; the Batch API
    does not accept triples). All triples are validated before the first call."""
    destination = Destination(graph_id=graph_id, user_id=user_id)
    if isinstance(triples, str | Path):
        materialized = _load_triples(Path(triples))
    else:
        materialized = list(triples)
    result = IngestResult(method="sequential", client=client)
    for index, triple in enumerate(materialized):
        kwargs = triple.to_api_kwargs(destination)
        _, error = call_with_retries(
            lambda: client.graph.add_fact_triple(**kwargs),  # noqa: B023
            max_retries=max_retries,
        )
        if error is not None:
            result.add_errors.append(
                AddError(
                    index=index,
                    item_count=1,
                    error=(
                        f"graph.add_fact_triple failed: status={error.status_code}, "
                        f"body={error.body}"
                    ),
                )
            )
        else:
            result.items_submitted += 1
    return result
