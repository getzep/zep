"""IngestResult: one result type unified over the batch and sequential paths."""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.exceptions import (
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    IngestUntrackedError,
)

if TYPE_CHECKING:
    from zep_cloud.client import Zep
    from zep_cloud.types.batch_item_detail import BatchItemDetail
    from zep_cloud.types.batch_summary import BatchSummary

# Batch statuses that will not change without further action.
_TERMINAL_BATCH_STATUSES = frozenset({"succeeded", "partial", "failed", "invalid", "canceled"})
_TERMINAL_TASK_STATUSES = frozenset({"succeeded", "partial", "failed", "canceled"})

# Terminal statuses that mean the run did not fully succeed. Derived from the terminal
# sets so a status added there cannot slip past raise_for_status(). "untracked" is
# excluded deliberately: with no completion handle the outcome is unknown rather than
# bad, and wait() already raises IngestUntrackedError for it.
_UNSUCCESSFUL_STATUSES = (_TERMINAL_BATCH_STATUSES | _TERMINAL_TASK_STATUSES) - {"succeeded"}

# Aggregation priority: the worst/least-done status wins.
_STATUS_PRIORITY = [
    "failed",
    "partial",
    "canceled",
    "untracked",
    "processing",
    "queued",
    "succeeded",
]


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


def _identity_from_task_params(params: Any) -> tuple[list[str], list[str]]:
    """Pull server-assigned identities out of completed-task params.

    After ``add_nodes`` succeeds the worker merges ``node_uuids`` into params;
    after ``add_fact_triple`` succeeds it merges ``edge_uuid``. Pending tasks
    do not carry these keys yet.
    """
    if not isinstance(params, dict):
        return [], []
    node_uuids: list[str] = []
    raw_nodes = params.get("node_uuids")
    if isinstance(raw_nodes, list):
        node_uuids = [str(item) for item in raw_nodes if item]
    elif isinstance(raw_nodes, str) and raw_nodes:
        node_uuids = [raw_nodes]
    edge_uuids: list[str] = []
    edge_uuid = params.get("edge_uuid")
    if edge_uuid:
        edge_uuids = [str(edge_uuid)]
    return node_uuids, edge_uuids


# Graph extraction is queued per destination. Wait budgets scale with how many
# items still have to drain through that queue, not with how many we poll.
SECONDS_PER_SUBMITTED_ITEM = 60.0
MIN_WAIT_TIMEOUT_SECONDS = 120.0


def wait_timeout_seconds(item_count: int) -> float:
    """Timeout for ``IngestResult.wait()`` given how many items were submitted.

    The graph processes episodes in order, so waiting on the last one still has
    to cover the work ahead of it. Floor is ``MIN_WAIT_TIMEOUT_SECONDS`` so a
    tiny run is not given an unrealistically short deadline.
    """
    require_int_range("item_count", item_count, minimum=0)
    return max(MIN_WAIT_TIMEOUT_SECONDS, item_count * SECONDS_PER_SUBMITTED_ITEM)


@dataclass(slots=True)
class AddError:
    """A submission failure: where it happened, and what the API said about it.

    The API's own message is reported unchanged, exactly as a direct SDK call
    would surface it. Nothing here is added from the submitted episodes."""

    index: int  # page index (batch) or episode stream index (sequential); -1 = batch-level
    item_count: int
    error: str
    batch_id: str | None = None


@dataclass
class IngestResult:
    """Outcome of an ingestion run.

    Stateless by design: everything recoverable comes from Batch API statuses or
    episode/task processing flags; ``batch_ids``/``episode_uuids``/``task_ids`` are the
    resume handles a caller can persist. ``node_uuids`` is parallel to the
    ``ingest_nodes`` input (assigned UUID or ``None`` for a failed/missing slot)
    so ``zip`` cannot pin a later success to an earlier failure; when resuming
    from task IDs only, UUIDs are recovered from completed task params in
    ``task_ids`` order. ``edge_uuids`` records fact identities from
    ``add_fact_triple`` task params in ``task_ids`` order (``None`` when a
    terminal task assigned none), stopping only before still-in-flight tasks so
    out-of-order completion cannot scramble zip order against the submitted
    triples. ``untracked_items`` records accepted writes for which the API
    returned no completion handle.
    """

    method: Literal["batch", "sequential"]
    items_submitted: int = 0
    batch_ids: list[str] = field(default_factory=list)
    episode_uuids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    node_uuids: list[str | None] = field(default_factory=list)
    edge_uuids: list[str | None] = field(default_factory=list)
    untracked_items: int = 0
    add_errors: list[AddError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    client: "Zep | None" = field(default=None, repr=False)
    _batch_summaries: "dict[str, BatchSummary]" = field(
        default_factory=dict, repr=False, compare=False
    )
    _processed_uuids: set[str] = field(default_factory=set, repr=False, compare=False)
    _task_statuses: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    # Cached task.params by task_id so identities can be rebuilt in task_ids order
    # even when a later task becomes terminal before an earlier one.
    _task_params: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    # True when node_uuids was filled from the add_nodes response (submission order).
    # Task-param recovery must not overwrite or reorder that list.
    _node_uuids_from_submit: bool = field(default=False, repr=False, compare=False)
    # Plain graph.add (and single-thread backfills) share one per-graph queue, so
    # polling the last UUID is enough. Multiple threads are independent sagas.
    _single_queue_episode_poll: bool = field(default=True, repr=False, compare=False)

    @classmethod
    def from_batch_ids(cls, client: "Zep", batch_ids: "Sequence[str]") -> "IngestResult":
        """Reconstruct a result from persisted batch ids — e.g. in a later process,
        after an ingest whose result was never waited on. refresh()/status/wait()/
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
        self._refresh_batches()
        self._refresh_episodes(only_last=False)
        self._refresh_tasks()

    def _refresh_batches(self, *, only_last: bool = False) -> None:
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot refresh.")
        batch_ids = self.batch_ids[-1:] if only_last and self.batch_ids else self.batch_ids
        for batch_id in batch_ids:
            summary = self._batch_summaries.get(batch_id)
            if summary is not None and summary.status in _TERMINAL_BATCH_STATUSES:
                continue
            self._batch_summaries[batch_id] = self.client.batch.get(batch_id)

    def _refresh_episodes(self, *, only_last: bool) -> None:
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot refresh.")
        if not self.episode_uuids:
            return
        if only_last and self._single_queue_episode_poll:
            uuids = [self.episode_uuids[-1]]
        elif only_last:
            uuids = list(dict.fromkeys(self.episode_uuids))
        else:
            uuids = self.episode_uuids
        for uuid in uuids:
            if uuid in self._processed_uuids:
                continue
            episode = self.client.graph.episode.get(uuid_=uuid)
            if episode.processed:
                self._processed_uuids.add(uuid)
        if (
            only_last
            and self._single_queue_episode_poll
            and self.episode_uuids[-1] in self._processed_uuids
        ):
            # Plain graph.add and single-thread backfills are ordered by submission
            # (or request-array order within a thread.add_messages call): the
            # tail finishing means the queue in front of it has drained.
            self._processed_uuids.update(self.episode_uuids)

    def _refresh_tasks(self) -> None:
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot refresh.")
        for task_id in self.task_ids:
            if self._task_statuses.get(task_id) in _TERMINAL_TASK_STATUSES:
                continue
            task = self.client.task.get(task_id)
            self._task_statuses[task_id] = _normalize_task_status(task.status)
            self._task_params[task_id] = getattr(task, "params", None)
        self._sync_identities_from_task_params()

    def _param_identity_prefix(self, *, kind: Literal["node", "edge"]) -> list[str | None]:
        """Identities from cached task params in ``task_ids`` order.

        Stops only at the first still-in-flight task, so a later finish cannot
        surface its UUID ahead of an earlier submission. A terminal task with no
        identity (failed/canceled) leaves a ``None`` gap so later successes are
        still collected and stay zip-aligned with ``task_ids``.
        """
        collected: list[str | None] = []
        for task_id in self.task_ids:
            nodes, edges = _identity_from_task_params(self._task_params.get(task_id))
            values = nodes if kind == "node" else edges
            if values:
                collected.extend(values)
                continue
            status = self._task_statuses.get(task_id)
            if status not in _TERMINAL_TASK_STATUSES:
                # Not finished (or not polled yet) — hide later successes for now.
                break
            # Terminal without an assigned identity: keep a slot for this task.
            collected.append(None)
        return collected

    def _sync_identities_from_task_params(self) -> None:
        """Rebuild param-sourced identities in ``task_ids`` order after each poll."""
        self.edge_uuids = self._param_identity_prefix(kind="edge")
        if not self._node_uuids_from_submit:
            self.node_uuids = self._param_identity_prefix(kind="node")

    @property
    def status(self) -> str:
        """Aggregate status, including explicit ``untracked`` completion state."""
        statuses: list[str] = []
        if self.batch_ids:
            for batch_id in self.batch_ids:
                summary = self._batch_summaries.get(batch_id)
                raw = summary.status if summary is not None else "queued"
                if raw in (None, "draft", "queued"):
                    statuses.append("queued")
                elif raw == "invalid":
                    statuses.append("failed")
                else:
                    statuses.append(str(raw))
        if self.episode_uuids:
            if len(self._processed_uuids) >= len(set(self.episode_uuids)):
                statuses.append("succeeded")
            else:
                statuses.append("processing")
        statuses.extend(self._task_statuses.get(task_id, "queued") for task_id in self.task_ids)
        if self.untracked_items:
            statuses.append("untracked")
        if self.add_errors:
            statuses.append("partial")
        if not statuses:
            return "succeeded"
        for candidate in _STATUS_PRIORITY:
            if candidate in statuses:
                return candidate
        return statuses[0]

    def combine(self, *others: "IngestResult") -> "IngestResult":
        """Merge later submits into this result so ``wait()`` can poll once.

        Submission is independent of processing: call every ingest into the same
        graph, combine the results, then ``wait()`` (or skip waiting entirely).
        Sequential vs batch only chooses the submit API; it does not mean
        "finish processing this file before sending the next."
        """
        parts = (self, *others)
        clients = {id(part.client): part.client for part in parts if part.client is not None}
        if len(clients) > 1:
            raise ConfigurationError("Cannot combine IngestResults from different Zep clients")
        client = next(iter(clients.values()), None)
        has_batches = any(part.batch_ids for part in parts)
        has_sequential_handles = any(part.episode_uuids or part.task_ids for part in parts)
        method: Literal["batch", "sequential"] = (
            "batch" if has_batches and not has_sequential_handles else "sequential"
        )
        combined = IngestResult(method=method, client=client)
        for part in parts:
            combined.items_submitted += part.items_submitted
            combined.untracked_items += part.untracked_items
            combined.batch_ids.extend(part.batch_ids)
            combined.episode_uuids.extend(part.episode_uuids)
            combined.task_ids.extend(part.task_ids)
            combined.add_errors.extend(part.add_errors)
            combined.warnings.extend(part.warnings)
            combined._batch_summaries.update(part._batch_summaries)
            combined._processed_uuids.update(part._processed_uuids)
            combined._task_statuses.update(part._task_statuses)
            combined._task_params.update(part._task_params)
            combined._single_queue_episode_poll = (
                combined._single_queue_episode_poll and part._single_queue_episode_poll
            )
        return combined

    def _queued_item_count(self) -> int | None:
        """Known queued work, or ``None`` when only opaque batch ids exist."""
        if self.items_submitted > 0:
            return self.items_submitted
        count = len(self.episode_uuids) + len(self.task_ids)
        if count > 0:
            return count
        if self.batch_ids:
            return None
        return 0

    def _resolve_wait_timeout(self, timeout: float | Literal["auto"] | None) -> float | None:
        if timeout is None:
            return None
        if timeout == "auto":
            count = self._queued_item_count()
            if count is None:
                # from_batch_ids() has no item count; do not invent a 120s cap.
                return None
            return wait_timeout_seconds(count)
        require_nonnegative_number("timeout", timeout)
        return timeout

    def wait(
        self,
        *,
        poll_interval: float = 10.0,
        timeout: float | Literal["auto"] | None = "auto",
    ) -> "IngestResult":
        """Poll until processing reaches a terminal state.

        Which handle ``wait()`` polls depends on how the data was submitted
        (see `Check data ingestion status
        <https://help.getzep.com/check-data-ingestion-status>`_):

        - **Batch API** (default for ``zep-ingest`` episodes and thread
          backfills): ``batch.get`` on the last batch id — not per-episode
          polling.
        - **Sequential ``graph.add``** (batch fallback): the last-submitted
          episode's ``processed`` flag. ``zep-ingest`` does not set
          ``document_id``, so submission order matches ingestion order for plain
          ``graph.add`` chunks.
        - **Sequential ``thread.add_messages``**: the last message UUID in the
          last request per thread (``message_uuids[-1]``). Regular
          ``thread.add_messages`` does not return a ``task_id`` — only
          ``add_messages_batch`` does.
        - **Tasks** (``add_nodes``, ``add_fact_triple``): every ``task_id`` on
          each tick — tasks are not the same FIFO as graph episodes.

        The default timeout scales with ``items_submitted``
        (``wait_timeout_seconds``). Pass ``timeout=None`` to wait without a
        deadline. Reconstructed ``from_batch_ids()`` results have no item
        count, so ``timeout="auto"`` also waits without a deadline.

        Do not mix Batch API and sequential ``graph.add`` into the same graph
        and expect one ``wait()`` to cover both — they are not globally
        serialized.

        Raises IngestTimeoutError on timeout, or IngestUntrackedError when the
        API returned no completion handle; the result stays usable.
        """
        require_nonnegative_number("poll_interval", poll_interval)
        resolved_timeout = self._resolve_wait_timeout(timeout)
        if self.untracked_items:
            raise IngestUntrackedError(
                f"Cannot wait for {self.untracked_items} submitted item(s): the API returned "
                "no completion handle. Use search_when_ready() or poll an application-specific "
                "read before querying the ingested data."
            )
        start = time.monotonic()
        while True:
            self._poll_for_wait()
            if self._is_terminal():
                return self
            if resolved_timeout is not None and time.monotonic() - start >= resolved_timeout:
                raise IngestTimeoutError(
                    f"Ingestion still {self.status!r} after {resolved_timeout}s; call wait() "
                    "again or inspect progress via refresh()/status."
                )
            time.sleep(poll_interval)

    def _poll_for_wait(self) -> None:
        """Poll the tail of each submission path, and always poll task handles.

        Tasks (nodes, triples) are not the same FIFO as graph episodes, so they
        are refreshed every tick even while the episode or batch tail is still
        in flight — otherwise a combined ``nodes.combine(docs).wait()`` would
        hide a failed seed until the episode batch finished.
        """
        if self.episode_uuids:
            self._refresh_episodes(only_last=True)
            if self.episode_uuids[-1] in self._processed_uuids:
                self._refresh_batches(only_last=False)
            if self.batch_ids:
                # Batch + sequential graph.add on one graph is discouraged, but
                # poll the batch tail each tick when both handles are present.
                self._refresh_batches(only_last=True)
        elif self.batch_ids:
            self._refresh_batches(only_last=True)
            last = self._batch_summaries.get(self.batch_ids[-1])
            if last is not None and last.status in _TERMINAL_BATCH_STATUSES:
                self._refresh_batches(only_last=False)
        self._refresh_tasks()

    def _is_terminal(self) -> bool:
        if self.untracked_items:
            return False
        if self.batch_ids:
            batches_terminal = all(
                (summary := self._batch_summaries.get(batch_id)) is not None
                and summary.status in _TERMINAL_BATCH_STATUSES
                for batch_id in self.batch_ids
            )
            if not batches_terminal:
                return False
        if self.episode_uuids and len(self._processed_uuids) < len(set(self.episode_uuids)):
            return False
        return all(
            self._task_statuses.get(task_id) in _TERMINAL_TASK_STATUSES
            for task_id in set(self.task_ids)
        )

    def failed_items(self, *, limit: int = 100) -> "list[BatchItemDetail | AddError]":
        """Failed item details from submission and server-side batch processing."""
        require_int_range("limit", limit, minimum=1)
        collected: list[BatchItemDetail | AddError] = list(self.add_errors[:limit])
        if len(collected) >= limit or not self.batch_ids:
            return collected[:limit]
        if self.client is None:
            raise RuntimeError("IngestResult has no client; cannot list failed items.")
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
        """Opt-in strictness: raise IngestFailedError if anything failed or was canceled.

        ``untracked`` is not treated as a failure: wait() raises
        IngestUntrackedError for it, and a missing completion handle means the
        outcome is unknown, not bad.
        """
        status = self.status
        if not self.add_errors and status not in _UNSUCCESSFUL_STATUSES:
            return
        if self.add_errors:
            detail = f"{len(self.add_errors)} submission error(s)"
        elif status == "canceled":
            detail = "canceled before all items were processed"
        else:
            detail = "no submission errors; the failure happened server-side"
        raise IngestFailedError(
            f"Ingestion finished with status {status!r}: {detail}. "
            "Inspect failed_items() and warnings for details."
        )
