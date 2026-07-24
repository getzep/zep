"""IngestResult: one result type unified over the batch and sequential paths."""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.exceptions import IngestFailedError, IngestTimeoutError

if TYPE_CHECKING:
    from zep_cloud.client import Zep
    from zep_cloud.types.batch_item_detail import BatchItemDetail
    from zep_cloud.types.batch_summary import BatchSummary

# Batch statuses that will not change without further action.
_TERMINAL_BATCH_STATUSES = frozenset({"succeeded", "partial", "failed", "invalid", "canceled"})
_TERMINAL_TASK_STATUSES = frozenset({"succeeded", "partial", "failed", "canceled"})

# Aggregation priority: the worst/least-done status wins.
_STATUS_PRIORITY = ["failed", "partial", "canceled", "processing", "queued", "succeeded"]


def _normalize_task_status(status: str | None) -> str:
    status = status.lower() if status is not None else None
    if status is None or status in {"created", "draft", "pending", "queued"}:
        return "queued"
    if status in {"in_progress", "processing", "running"}:
        return "processing"
    if status in {"complete", "completed", "succeeded"}:
        return "succeeded"
    if status in {"cancelled", "canceled"}:
        return "canceled"
    if status in {"error", "failed"}:
        return "failed"
    return status


@dataclass(slots=True)
class AddError:
    """A submission failure. Carries indices and the API message — never episode
    content, which may be sensitive."""

    index: int  # page index (batch) or episode stream index (sequential); -1 = batch-level
    item_count: int
    error: str
    batch_id: str | None = None


@dataclass
class IngestResult:
    """Outcome of an ingestion run.

    Stateless by design: everything recoverable comes from Batch API statuses or
    episode/task processing flags; ``batch_ids``/``episode_uuids``/``task_ids`` are the resume
    handles a caller can persist.
    """

    method: Literal["batch", "sequential"]
    items_submitted: int = 0
    batch_ids: list[str] = field(default_factory=list)
    episode_uuids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    add_errors: list[AddError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    client: "Zep | None" = field(default=None, repr=False)
    _batch_summaries: "dict[str, BatchSummary]" = field(
        default_factory=dict, repr=False, compare=False
    )
    _processed_uuids: set[str] = field(default_factory=set, repr=False, compare=False)
    _task_statuses: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_batch_ids(cls, client: "Zep", batch_ids: "Sequence[str]") -> "IngestResult":
        """Reconstruct a result from persisted batch ids — e.g. in a later process,
        after running an ingest without wait=True. refresh()/status/wait()/
        failed_items() work as if the original result had been kept."""
        return cls(method="batch", batch_ids=list(batch_ids), client=client)

    @classmethod
    def from_task_ids(cls, client: "Zep", task_ids: "Sequence[str]") -> "IngestResult":
        """Reconstruct a task-backed sequential result from persisted task IDs."""
        return cls(method="sequential", task_ids=list(task_ids), client=client)

    def mark_batch_failed(self, batch_id: str, error: str) -> None:
        """Record a batch whose processing could not be triggered: an AddError is
        added and the batch's status is pinned to "failed" so status/wait() treat
        it as terminal (refresh() never overwrites a terminal summary)."""
        from zep_cloud.types.batch_summary import BatchSummary

        self.add_errors.append(AddError(index=-1, item_count=0, error=error, batch_id=batch_id))
        self._batch_summaries[batch_id] = BatchSummary(batch_id=batch_id, status="failed")

    def refresh(self) -> None:
        """Fetch the latest processing state from the API."""
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot refresh.")
        if self.method == "batch":
            for batch_id in self.batch_ids:
                summary = self._batch_summaries.get(batch_id)
                if summary is not None and summary.status in _TERMINAL_BATCH_STATUSES:
                    continue
                self._batch_summaries[batch_id] = self.client.batch.get(batch_id)
        else:
            for uuid in self.episode_uuids:
                if uuid in self._processed_uuids:
                    continue
                episode = self.client.graph.episode.get(uuid_=uuid)
                if episode.processed:
                    self._processed_uuids.add(uuid)
            for task_id in self.task_ids:
                if self._task_statuses.get(task_id) in _TERMINAL_TASK_STATUSES:
                    continue
                task = self.client.task.get(task_id)
                self._task_statuses[task_id] = _normalize_task_status(task.status)

    @property
    def status(self) -> str:
        """Aggregate status: failed > partial > canceled > processing > queued > succeeded."""
        statuses: list[str] = []
        if self.method == "batch":
            for batch_id in self.batch_ids:
                summary = self._batch_summaries.get(batch_id)
                raw = summary.status if summary is not None else "queued"
                if raw in (None, "draft", "queued"):
                    statuses.append("queued")
                elif raw == "invalid":
                    statuses.append("failed")
                else:
                    statuses.append(str(raw))
        else:
            if self.episode_uuids:
                if len(self._processed_uuids) >= len(set(self.episode_uuids)):
                    statuses.append("succeeded")
                else:
                    statuses.append("processing")
            statuses.extend(self._task_statuses.get(task_id, "queued") for task_id in self.task_ids)
        if self.add_errors:
            statuses.append("partial")
        if not statuses:
            return "succeeded"
        for candidate in _STATUS_PRIORITY:
            if candidate in statuses:
                return candidate
        return statuses[0]

    def wait(self, *, poll_interval: float = 10.0, timeout: float | None = None) -> "IngestResult":
        """Poll until processing reaches a terminal state.

        Raises IngestTimeoutError on timeout; the result stays usable.
        """
        require_nonnegative_number("poll_interval", poll_interval)
        if timeout is not None:
            require_nonnegative_number("timeout", timeout)
        start = time.monotonic()
        while True:
            self.refresh()
            if self._is_terminal():
                return self
            if timeout is not None and time.monotonic() - start >= timeout:
                raise IngestTimeoutError(
                    f"Ingestion still {self.status!r} after {timeout}s; call wait() "
                    "again or inspect progress via refresh()/status."
                )
            time.sleep(poll_interval)

    def _is_terminal(self) -> bool:
        if self.method == "batch":
            return all(
                (summary := self._batch_summaries.get(batch_id)) is not None
                and summary.status in _TERMINAL_BATCH_STATUSES
                for batch_id in self.batch_ids
            )
        episodes_terminal = len(self._processed_uuids) >= len(set(self.episode_uuids))
        tasks_terminal = all(
            self._task_statuses.get(task_id) in _TERMINAL_TASK_STATUSES
            for task_id in set(self.task_ids)
        )
        return episodes_terminal and tasks_terminal

    def failed_items(self, *, limit: int = 100) -> "list[BatchItemDetail] | list[AddError]":
        """Failed item details: Batch API item records (batch) or AddErrors (sequential)."""
        require_int_range("limit", limit, minimum=1)
        if self.method == "sequential":
            return self.add_errors[:limit]
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot list failed items.")
        collected: list[BatchItemDetail] = []
        for batch_id in self.batch_ids:
            cursor: int | None = None
            while len(collected) < limit:
                response = self.client.batch.list_items(
                    batch_id, status="failed", limit=limit - len(collected), cursor=cursor
                )
                collected.extend(response.items or [])
                cursor = response.next_cursor
                if cursor is None:
                    break
            if len(collected) >= limit:
                break
        return collected[:limit]

    def raise_for_status(self) -> None:
        """Opt-in strictness: raise IngestFailedError if anything failed."""
        if self.add_errors or self.status in ("failed", "partial"):
            raise IngestFailedError(
                f"Ingestion finished with status {self.status!r}: "
                f"{len(self.add_errors)} submission error(s). "
                "Inspect failed_items() and warnings for details."
            )
