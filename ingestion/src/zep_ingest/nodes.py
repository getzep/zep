"""Batch node seeding for canonical entities, independent of episode extraction."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types.add_nodes_result import AddNodesResult
from zep_cloud.types.node_input import NodeInput

from zep_ingest._errors import format_api_error
from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest._validation import check_len, check_required_string, check_scalar_map
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.result import AddError, IngestResult, task_uuid_of
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.types import Destination

MAX_NODE_NAME_CHARS = 50
MAX_SUMMARY_CHARS = 500
MAX_LABEL_CHARS = 100
MAX_ATTRIBUTE_KEYS = 10
MAX_NODES_PER_REQUEST = 100


@dataclass(slots=True)
class NodeItem:
    """One canonical entity node, validated against the batch-node API limits.

    Zep assigns each node's UUID. Capture the returned values from
    ``IngestResult.node_uuids`` (parallel to the submitted nodes; ``None`` where
    a batch failed) if you need them for updates or fact-triple pinning.
    """

    name: str
    label: str | None = None
    summary: str | None = None
    attributes: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        check_required_string("name", self.name, MAX_NODE_NAME_CHARS, errors)
        check_len("summary", self.summary, MAX_SUMMARY_CHARS, errors)
        check_len("label", self.label, MAX_LABEL_CHARS, errors)
        check_scalar_map("attributes", self.attributes, errors, max_keys=MAX_ATTRIBUTE_KEYS)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_ATTRIBUTE_KEYS)
        if errors:
            raise ConfigurationError(f"Invalid node {str(self.name)[:40]!r}: " + "; ".join(errors))

    def to_node_input(self) -> NodeInput:
        """Build the SDK request model, omitting unset fields.

        Only fields that are actually set are passed, so an unset field is
        omitted from the request rather than sent as ``null``.
        """
        fields: dict[str, Any] = {"name": self.name}
        if self.label is not None:
            fields["label"] = self.label
        if self.summary is not None:
            fields["summary"] = self.summary
        if self.attributes is not None:
            fields["attributes"] = self.attributes
        if self.metadata is not None:
            fields["metadata"] = self.metadata
        return NodeInput(**fields)


_RETIRED_NODE_FIELDS = {
    "uuid": (
        "uuid cannot be supplied: Zep assigns node UUIDs "
        "(use client.graph.node.update with a UUID from IngestResult.node_uuids "
        "to update an existing node)"
    ),
    "created_at": (
        "created_at cannot be supplied: the v4 node model has no reference time "
        "(Zep sets the node's creation time)"
    ),
}


def _load_nodes(path: Path) -> list[NodeItem]:
    rows = rows_to_fields(load_rows(path), NodeItem, retired_fields=_RETIRED_NODE_FIELDS)
    return [NodeItem(**row) for row in rows]


def _assigned_node_uuids(response: AddNodesResult, *, expected: int) -> list[str | None]:
    """Extract Zep-assigned node UUIDs from a ``graph.node.add`` response.

    The returned list is always ``expected`` long and aligned with the request
    batch: a missing entry is ``None`` so callers can zip against the submitted
    nodes without shifting later identities forward over a gap.
    """
    nodes = response.nodes or []
    uuids: list[str | None] = []
    for index in range(expected):
        node_uuid = nodes[index].uuid_ if index < len(nodes) else None
        uuids.append(node_uuid or None)
    return uuids


def ingest_nodes(
    client: Zep,
    nodes: Iterable[NodeItem] | str | Path,
    *,
    graph_uuid: str | None = None,
    batch_size: int = MAX_NODES_PER_REQUEST,
    max_retries: int = 5,
) -> IngestResult:
    """Create canonical nodes via ``client.graph.node.add``.

    Zep assigns each node's UUID. ``result.node_uuids`` is parallel to the
    submitted node list: successes carry the assigned UUID, and a failed batch
    (or a missing response entry) leaves ``None`` in those slots so a later
    success cannot shift forward under ``zip``. Submission is asynchronous; bind
    the result, then wait on it, so the resume handles survive a timeout::

        result = ingest_nodes(client, nodes, graph_uuid=graph_uuid)
        result.wait()
        # result.node_uuids[i] matches the i-th submitted node (or None)
    """
    destination = Destination(graph_uuid=graph_uuid)
    if not 1 <= batch_size <= MAX_NODES_PER_REQUEST:
        raise ConfigurationError(f"batch_size must be 1..{MAX_NODES_PER_REQUEST}, got {batch_size}")
    materialized = _load_nodes(Path(nodes)) if isinstance(nodes, str | Path) else list(nodes)

    target = destination.graph
    result = IngestResult(method="sequential", client=client, graph_uuid=target)
    for start in range(0, len(materialized), batch_size):
        batch = materialized[start : start + batch_size]
        items = [node.to_node_input() for node in batch]
        response, error = call_with_retries(
            lambda: client.graph.node.add(graph_uuid=target, nodes=items),  # noqa: B023
            max_retries=max_retries,
        )
        # Always extend node_uuids by batch length so indices stay aligned with
        # the submitted list across partial failures.
        result._node_uuids_from_submit = True
        if error is not None:
            result.add_errors.append(
                AddError(
                    index=start,
                    item_count=len(batch),
                    error=format_api_error("graph.node.add", error),
                )
            )
            result.node_uuids.extend([None] * len(batch))
            continue
        result.items_submitted += len(batch)
        result.node_uuids.extend(_assigned_node_uuids(response, expected=len(batch)))
        task_id = task_uuid_of(response.task)
        if task_id and task_id not in result.task_ids:
            result.task_ids.append(task_id)
        elif not task_id:
            result.untracked_items += len(batch)
    return result
