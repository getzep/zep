"""Core data model: Episode, Destination, API limits, and API mappings."""

from dataclasses import dataclass, field
from typing import Any, Literal

from zep_cloud.types.batch_add_item import BatchAddItem

from zep_ingest._validation import check_len, check_scalar_map, check_timestamp
from zep_ingest.exceptions import ConfigurationError

# Documented Zep API limits (see help.getzep.com/adding-batch-data and
# help.getzep.com/adding-business-data).
MAX_EPISODE_CHARS = 10_000
SAFE_EPISODE_CHARS = 9_500  # LimitGuard target; headroom for context prefixes
MAX_ITEMS_PER_ADD = 350
MAX_ITEMS_PER_BATCH = 50_000
MAX_METADATA_KEYS = 10

DataType = Literal["text", "json", "message"]


@dataclass(slots=True)
class Episode:
    """One unit of data to ingest into a Zep graph.

    ``document`` is internal plumbing: the chunker sets it to the full source
    document when it splits, and the contextualizer consumes it. It is never
    sent to the API.
    """

    data: str
    data_type: DataType = "text"
    created_at: str | None = None  # RFC3339; loaders populate from source timestamps
    metadata: dict[str, Any] | None = None
    source_description: str | None = None
    document: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.data, str) or not self.data.strip():
            errors.append("data must be a non-empty string")
        if self.data_type not in ("text", "json", "message"):
            errors.append(
                f"data_type must be one of ['json', 'message', 'text'], got {self.data_type!r}"
            )
        check_timestamp("created_at", self.created_at, errors)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_METADATA_KEYS)
        check_len("source_description", self.source_description, 500, errors)
        if self.document is not None and not isinstance(self.document, str):
            errors.append(f"document must be a string, got {type(self.document).__name__}")
        if errors:
            raise ConfigurationError("Invalid episode: " + "; ".join(errors))


@dataclass(frozen=True, slots=True)
class Destination:
    """Target graph for an ingestion run: exactly one of graph_id or user_id."""

    graph_id: str | None = None
    user_id: str | None = None

    def __post_init__(self) -> None:
        if bool(self.graph_id) == bool(self.user_id):
            raise ConfigurationError(
                "Destination requires exactly one of graph_id (a named graph) or "
                "user_id (a user graph). "
                f"Got graph_id={self.graph_id!r}, user_id={self.user_id!r}."
            )


def _capped_metadata(
    metadata: dict[str, Any] | None, warnings: list[str] | None
) -> dict[str, Any] | None:
    # Episode validates this at construction. Keep the helper for API mapping
    # compatibility, but never silently discard caller metadata.
    del warnings
    return metadata


def to_batch_item(
    episode: Episode, destination: Destination, warnings: list[str] | None = None
) -> BatchAddItem:
    """Map a validated Episode to a Batch API item."""
    return BatchAddItem(
        type="graph_episode",
        data=episode.data,
        data_type=episode.data_type,
        created_at=episode.created_at,
        metadata=_capped_metadata(episode.metadata, warnings),
        source_description=episode.source_description,
        graph_id=destination.graph_id,
        user_id=destination.user_id,
    )


def to_graph_add_kwargs(
    episode: Episode, destination: Destination, warnings: list[str] | None = None
) -> dict[str, Any]:
    """Map an Episode to graph.add(**kwargs) (unset optional fields omitted)."""
    kwargs: dict[str, Any] = {"data": episode.data, "type": episode.data_type}
    if episode.created_at is not None:
        kwargs["created_at"] = episode.created_at
    metadata = _capped_metadata(episode.metadata, warnings)
    if metadata is not None:
        kwargs["metadata"] = metadata
    if episode.source_description is not None:
        kwargs["source_description"] = episode.source_description
    if destination.graph_id is not None:
        kwargs["graph_id"] = destination.graph_id
    if destination.user_id is not None:
        kwargs["user_id"] = destination.user_id
    return kwargs
