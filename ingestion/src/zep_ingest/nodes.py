"""Batch node seeding for canonical entities, independent of episode extraction."""

import uuid as uuid_module
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types.add_node_item import AddNodeItem

from zep_ingest._errors import safe_api_error
from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest._validation import (
    check_len,
    check_required_string,
    check_scalar_map,
    check_timestamp,
)
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.types import Destination

MAX_NODE_NAME_CHARS = 50
MAX_SUMMARY_CHARS = 500
MAX_LABEL_CHARS = 100
MAX_ATTRIBUTE_KEYS = 10
MAX_NODES_PER_REQUEST = 100


@dataclass(slots=True)
class NodeItem:
    """One canonical entity node, validated against the batch-node API limits."""

    name: str
    label: str | None = None
    summary: str | None = None
    attributes: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    uuid: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        check_required_string("name", self.name, MAX_NODE_NAME_CHARS, errors)
        check_len("summary", self.summary, MAX_SUMMARY_CHARS, errors)
        check_len("label", self.label, MAX_LABEL_CHARS, errors)
        check_scalar_map("attributes", self.attributes, errors, max_keys=MAX_ATTRIBUTE_KEYS)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_ATTRIBUTE_KEYS)
        check_timestamp("created_at", self.created_at, errors)
        if self.uuid is not None:
            try:
                parsed = uuid_module.UUID(str(self.uuid))
                if parsed.version != 4:
                    errors.append(f"uuid must be UUIDv4 (got version {parsed.version})")
            except (ValueError, AttributeError, TypeError):
                errors.append(f"uuid is not a valid UUID: {self.uuid!r}")
        if errors:
            raise ConfigurationError(f"Invalid node {str(self.name)[:40]!r}: " + "; ".join(errors))

    def to_add_node_item(self) -> AddNodeItem:
        """Build the SDK request model, omitting unset fields.

        Our ``uuid`` maps to the SDK's ``uuid_`` field, which the client
        serializes to the wire ``uuid`` key. Only fields that are actually set
        are passed, so an unset field is omitted from the request rather than
        sent as ``null`` (which matters for upserts, where a null could clobber
        an existing value).
        """
        fields: dict[str, Any] = {"name": self.name}
        if self.uuid is not None:
            fields["uuid_"] = self.uuid
        if self.label is not None:
            fields["label"] = self.label
        if self.summary is not None:
            fields["summary"] = self.summary
        if self.attributes:
            fields["attributes"] = self.attributes
        if self.metadata:
            fields["metadata"] = self.metadata
        if self.created_at is not None:
            fields["created_at"] = self.created_at
        return AddNodeItem(**fields)


def _load_nodes(path: Path) -> list[NodeItem]:
    rows = load_rows(path)
    fields = frozenset({"name", "label", "summary", "attributes", "metadata", "uuid", "created_at"})
    return [NodeItem(**row) for row in rows_to_fields(rows, fields)]


def ingest_nodes(
    client: Zep,
    nodes: Iterable[NodeItem] | str | Path,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    batch_size: int = MAX_NODES_PER_REQUEST,
    max_retries: int = 5,
    require_uuids: bool = True,
) -> IngestResult:
    """Create/upsert canonical nodes via ``client.graph.add_nodes``.

    UUID is the only safe idempotency key. By default every node must have a
    persisted UUIDv4; callers must explicitly opt out of that protection.
    UUID uniqueness is checked before the first network call.
    """
    destination = Destination(graph_id=graph_id, user_id=user_id)
    if not 1 <= batch_size <= MAX_NODES_PER_REQUEST:
        raise ConfigurationError(f"batch_size must be 1..{MAX_NODES_PER_REQUEST}, got {batch_size}")
    materialized = _load_nodes(Path(nodes)) if isinstance(nodes, str | Path) else list(nodes)
    missing = [node.name for node in materialized if node.uuid is None]
    if missing and require_uuids:
        sample = ", ".join(repr(name) for name in missing[:3])
        raise ConfigurationError(
            f"{len(missing)} node(s) have no persisted UUIDv4 ({sample}). "
            "Set require_uuids=False only for an intentionally non-idempotent ingest."
        )
    uuids = [str(node.uuid) for node in materialized if node.uuid is not None]
    if len(uuids) != len(set(uuids)):
        raise ConfigurationError("node UUIDs must be unique within an ingestion plan")

    scope = (
        {"graph_id": destination.graph_id}
        if destination.graph_id is not None
        else {"user_id": destination.user_id}
    )
    result = IngestResult(method="sequential", client=client)
    if missing:
        result.warnings.append(f"{len(missing)} node(s) have no UUID and may duplicate on a rerun.")
    for start in range(0, len(materialized), batch_size):
        batch = materialized[start : start + batch_size]
        items = [node.to_add_node_item() for node in batch]
        response, error = call_with_retries(
            lambda: client.graph.add_nodes(nodes=items, **scope),  # noqa: B023
            max_retries=max_retries,
        )
        if error is not None:
            result.add_errors.append(
                AddError(
                    index=start,
                    item_count=len(batch),
                    error=safe_api_error("graph.add_nodes", error),
                )
            )
            continue
        result.items_submitted += len(batch)
        task_id = getattr(response, "task_id", None)
        if task_id and str(task_id) not in result.task_ids:
            result.task_ids.append(str(task_id))
    return result
