"""Core data model: Episode, Destination, API limits, and API mappings."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from zep_cloud.types.batch_item_input import BatchItemInput

from zep_ingest._validation import check_scalar_map, check_timestamp
from zep_ingest.exceptions import ConfigurationError

# Documented Zep API limits (see help.getzep.com/adding-batch-data,
# help.getzep.com/adding-business-data and help.getzep.com/adding-messages).
MAX_EPISODE_CHARS = 10_000
SAFE_EPISODE_CHARS = 9_500  # LimitGuard target; headroom for context prefixes
MAX_ITEMS_PER_ADD = 350
MAX_ITEMS_PER_BATCH = 50_000  # documented API cap
DEFAULT_ITEMS_PER_BATCH = 10_000  # default rollover; configurable up to MAX_ITEMS_PER_BATCH
MAX_MESSAGES_PER_THREAD_ADD = 30  # thread.add_messages per call; not MAX_ITEMS_PER_ADD
MAX_METADATA_KEYS = 10

DataType = Literal["text", "json", "message"]
#: A glob, a single path, or several globs/paths ingested in caller order.
SourcePaths = str | Path | Sequence[str | Path]


@dataclass(slots=True)
class Episode:
    """One unit of data to ingest into a Zep graph.

    Provenance travels in ``metadata``: loaders stamp a ``source_type`` on every
    episode (``document``, ``slack``, ``transcript``, ``email``, ``json_record``)
    plus source-specific keys (a document's ``file_name``, a Slack ``channel`` /
    ``thread_ts``, and so on) — structured, queryable fields rather than one
    free-text description.

    ``document`` is internal plumbing: the chunker sets it to the full source
    document when it splits, and the contextualizer consumes it. It is never
    sent to the API.
    """

    data: str
    data_type: DataType = "text"
    created_at: str | None = None  # RFC3339; loaders populate from source timestamps
    metadata: dict[str, Any] | None = None
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
        if self.document is not None and not isinstance(self.document, str):
            errors.append(f"document must be a string, got {type(self.document).__name__}")
        if errors:
            raise ConfigurationError("Invalid episode: " + "; ".join(errors))


@dataclass(frozen=True, slots=True)
class Destination:
    """Target graph for an ingestion run, addressed by its UUID.

    Zep v4 addresses every graph by a server-generated UUID. A user graph is
    addressed by the same field: read ``user.graph_uuid`` one time, store it,
    and pass it here. A ``graph_id`` is a name, not an address.
    """

    graph_uuid: str | None = None

    def __post_init__(self) -> None:
        if not self.graph_uuid or not self.graph_uuid.strip():
            raise ConfigurationError(
                "Destination requires graph_uuid, the UUID of the target graph. "
                "For a user graph, pass the user's graph_uuid. "
                f"Got graph_uuid={self.graph_uuid!r}."
            )

    @property
    def graph(self) -> str:
        """The target graph UUID, narrowed for the API calls that require it."""
        return str(self.graph_uuid)


def to_batch_item(episode: Episode, destination: Destination) -> BatchItemInput:
    """Map a validated Episode to a Batch API item.

    The v4 batch item model has no ``created_at`` field, so an episode's
    reference time is not carried on this path. Submit such episodes with
    ``method="sequential"`` to keep their reference time.
    """
    return BatchItemInput(
        type="graph_episode",
        data=episode.data,
        data_type=episode.data_type,
        metadata=episode.metadata,
        graph_uuid=destination.graph_uuid,
    )


def to_episode_add_kwargs(episode: Episode, destination: Destination) -> dict[str, Any]:
    """Map an Episode to graph.episode.add(**kwargs) (unset fields omitted)."""
    kwargs: dict[str, Any] = {
        "graph_uuid": destination.graph,
        "data": episode.data,
        "type": episode.data_type,
    }
    if episode.created_at is not None:
        kwargs["created_at"] = episode.created_at
    if episode.metadata is not None:
        kwargs["metadata"] = episode.metadata
    return kwargs
