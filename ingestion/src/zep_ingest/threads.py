"""User-data ingestion: chat history backfills into user graphs via threads.

Business data goes to named graphs as episodes; a user's own conversations go
to their user graph as thread messages. This module owns that path end to end:

- validates every message client-side (role, content, timestamps, metadata)
  before any API call,
- pre-creates the user and the destination threads (the Batch API requires
  threads to exist),
- auto-splits messages over the 4,096-character message limit at sentence
  boundaries instead of letting the API reject them,
- submits via Batch API ``thread_message`` items when the plan allows it and
  the messages carry no ``created_at`` (the Batch API currently drops those
  timestamps), and via plain ``thread.add_messages`` otherwise — preserving
  per-thread chronological order either way.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.errors.not_found_error import NotFoundError
from zep_cloud.types.batch_add_item import BatchAddItem
from zep_cloud.types.message import Message

from zep_ingest._errors import safe_api_error
from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest._validation import (
    check_len,
    check_scalar_map,
    check_timestamp,
    require_int_range,
)
from zep_ingest.exceptions import BatchUnavailableError, ConfigurationError
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.batch import is_gating_error, process_batch
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.transforms._splitting import split_text
from zep_ingest.types import MAX_ITEMS_PER_ADD, MAX_ITEMS_PER_BATCH, MAX_METADATA_KEYS

logger = logging.getLogger("zep_ingest")

#: Documented per-message content limit for thread messages (thread.add_messages
#: and thread_message batch items alike) — distinct from the 10k episode limit.
MAX_MESSAGE_CHARS = 4096
_SPLIT_TARGET = 4000  # headroom under the hard limit

ROLE_TYPES = frozenset({"user", "assistant", "system", "function", "tool", "norole"})


@dataclass(slots=True)
class ThreadMessage:
    """One chat message destined for a user's thread, validated client-side."""

    thread_id: str
    content: str
    role: str = "user"
    name: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.thread_id, str) or not self.thread_id.strip():
            errors.append("thread_id must be a non-empty string")
        if not isinstance(self.content, str) or not self.content.strip():
            errors.append("content must be a non-empty string")
        if not isinstance(self.role, str) or self.role not in ROLE_TYPES:
            errors.append(f"role must be one of {sorted(ROLE_TYPES)}, got {self.role!r}")
        check_len("name", self.name, 100, errors)
        check_timestamp("created_at", self.created_at, errors)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_METADATA_KEYS)
        if errors:
            raise ConfigurationError(
                f"Invalid thread message (thread {self.thread_id!r}): " + "; ".join(errors)
            )


_MESSAGE_FIELDS = frozenset(ThreadMessage.__dataclass_fields__)


def _load_messages(path: Path) -> list[ThreadMessage]:
    rows = rows_to_fields(load_rows(path), _MESSAGE_FIELDS)
    return [ThreadMessage(**row) for row in rows]


def _prepare(messages: list[ThreadMessage], warnings: list[str]) -> list[ThreadMessage]:
    """Split oversize contents and collect temporal warnings."""
    prepared: list[ThreadMessage] = []
    split_count = 0
    missing_timestamps = 0
    for message in messages:
        if message.created_at is None:
            missing_timestamps += 1
        if len(message.content) <= MAX_MESSAGE_CHARS:
            prepared.append(message)
            continue
        split_count += 1
        for piece in split_text(message.content, _SPLIT_TARGET):
            prepared.append(replace(message, content=piece))
    if split_count:
        warnings.append(
            f"{split_count} message(s) exceeded the {MAX_MESSAGE_CHARS}-character "
            "thread-message limit and were split at sentence boundaries."
        )
    if missing_timestamps:
        warnings.append(
            f"{missing_timestamps} message(s) have no created_at timestamp. Zep "
            "defaults to the ingestion time, which corrupts fact validity "
            "timelines on backfills."
        )
    return prepared


def _ensure_user_and_threads(client: Zep, user_id: str, messages: list[ThreadMessage]) -> None:
    try:
        client.user.get(user_id)
    except NotFoundError:
        client.user.add(user_id=user_id)
    seen: set[str] = set()
    for message in messages:
        if message.thread_id in seen:
            continue
        seen.add(message.thread_id)
        try:
            client.thread.create(thread_id=message.thread_id, user_id=user_id)
        except ApiError as error:
            # The API reports an existing thread as 400 "already exists" (or
            # 409); any other 400 is a real validation error and must surface.
            already_exists = error.status_code == 409 or (
                error.status_code == 400 and "already exists" in str(error.body)
            )
            if not already_exists:
                raise


def _submit_batch(
    client: Zep,
    messages: list[ThreadMessage],
    *,
    batch_metadata: dict[str, Any] | None,
    max_retries: int,
) -> IngestResult:
    result = IngestResult(method="batch", client=client)
    iterator = iter(messages)
    batch_id: str | None = None
    items_in_batch = 0
    page_index = 0
    while True:
        page = list(islice(iterator, MAX_ITEMS_PER_ADD))
        if not page:
            break
        if batch_id is not None and items_in_batch + len(page) > MAX_ITEMS_PER_BATCH:
            process_batch(client, batch_id, result, max_retries=max_retries)
            batch_id = None
        if batch_id is None:
            try:
                if batch_metadata is not None:
                    summary = client.batch.create(metadata=batch_metadata)
                else:
                    summary = client.batch.create()
            except ApiError as error:
                if is_gating_error(error):
                    raise BatchUnavailableError(partial_result=result) from error
                raise
            batch_id = summary.batch_id or ""
            result.batch_ids.append(batch_id)
            items_in_batch = 0
        items = [
            BatchAddItem(
                type="thread_message",
                thread_id=message.thread_id,
                content=message.content,
                role=message.role,  # type: ignore[arg-type]
                name=message.name,
                created_at=message.created_at,
                metadata=message.metadata,
            )
            for message in page
        ]
        current_batch = batch_id
        _, add_failure = call_with_retries(
            lambda: client.batch.add(current_batch, items=items),  # noqa: B023
            max_retries=max_retries,
        )
        if add_failure is not None:
            result.add_errors.append(
                AddError(
                    index=page_index,
                    item_count=len(page),
                    error=(safe_api_error("batch.add", add_failure)),
                    batch_id=batch_id,
                )
            )
        else:
            result.items_submitted += len(page)
            items_in_batch += len(page)
        page_index += 1
    if batch_id is not None:
        process_batch(client, batch_id, result, max_retries=max_retries)
    return result


def _submit_sequential(
    client: Zep,
    messages: list[ThreadMessage],
    *,
    messages_per_call: int,
    max_retries: int,
) -> IngestResult:
    result = IngestResult(method="sequential", client=client)
    by_thread: dict[str, list[ThreadMessage]] = {}
    for message in messages:
        by_thread.setdefault(message.thread_id, []).append(message)
    chunk_index = 0
    for thread_id, thread_messages in by_thread.items():
        for start in range(0, len(thread_messages), messages_per_call):
            chunk = thread_messages[start : start + messages_per_call]
            payload = [
                Message(
                    content=m.content,
                    role=m.role,  # type: ignore[arg-type]
                    name=m.name,
                    created_at=m.created_at,
                    metadata=m.metadata,
                )
                for m in chunk
            ]
            _, error = call_with_retries(
                lambda: client.thread.add_messages(thread_id, messages=payload),  # noqa: B023
                max_retries=max_retries,
            )
            if error is not None:
                result.add_errors.append(
                    AddError(
                        index=chunk_index,
                        item_count=len(chunk),
                        error=safe_api_error(f"thread.add_messages({thread_id!r})", error),
                    )
                )
            else:
                result.items_submitted += len(chunk)
            chunk_index += 1
    return result


def ingest_thread_messages(
    client: Zep,
    messages: Iterable[ThreadMessage] | str | Path,
    *,
    user_id: str | None = None,
    method: Literal["auto", "batch", "sequential"] = "auto",
    batch_metadata: dict[str, Any] | None = None,
    messages_per_call: int = 30,
    max_retries: int = 5,
    thread_id_suffix: str | None = None,
) -> IngestResult:
    """Backfill chat history into a user's graph via threads.

    Accepts ThreadMessage objects or a JSONL / JSON-array path with columns
    thread_id/role/name/content/created_at. The user and every referenced
    thread are created if missing (the Batch API requires threads to exist);
    per-thread message order is preserved on both submission paths.

    Thread ids are global to a Zep project — pass ``thread_id_suffix`` to
    namespace them (e.g. per environment or per re-run) without rewriting
    your source data.
    """
    if not user_id:
        raise ConfigurationError(
            "ingest_thread_messages requires user_id — threads belong to a user "
            "and their messages land on that user's graph."
        )
    if method not in ("auto", "batch", "sequential"):
        raise ConfigurationError(
            f"method must be one of ['auto', 'batch', 'sequential'], got {method!r}"
        )
    require_int_range("messages_per_call", messages_per_call, minimum=1)
    require_int_range("max_retries", max_retries, minimum=1)
    if thread_id_suffix is not None and not isinstance(thread_id_suffix, str):
        raise ConfigurationError("thread_id_suffix must be a string or None")
    if isinstance(messages, str | Path):
        materialized = _load_messages(Path(messages))
    else:
        materialized = list(messages)
    if thread_id_suffix:
        materialized = [
            replace(m, thread_id=f"{m.thread_id}{thread_id_suffix}") for m in materialized
        ]
    warnings: list[str] = []
    prepared = _prepare(materialized, warnings)
    _ensure_user_and_threads(client, user_id, prepared)

    # The Batch API currently ignores created_at on thread_message items
    # (verified against the live API 2026-07): a batch backfill silently dates
    # every message at ingestion time, corrupting fact validity timelines.
    has_timestamps = any(m.created_at is not None for m in prepared)
    if method == "auto" and has_timestamps:
        notice = (
            "Messages carry created_at timestamps, but the Zep Batch API currently "
            "ignores created_at on thread_message items — using sequential "
            "thread.add_messages to preserve your timeline. Pass method='batch' to "
            "force the batch path anyway."
        )
        if batch_metadata is not None:
            notice += " Note: batch_metadata does not apply on the sequential path."
        warnings.append(notice)
        method = "sequential"
    elif method == "batch" and has_timestamps:
        warnings.append(
            "The Zep Batch API currently ignores created_at on thread_message items: "
            "these messages will be dated at ingestion time, not their created_at. "
            "Use method='sequential' (or 'auto') to preserve backfill timelines."
        )

    if method == "sequential":
        result = _submit_sequential(
            client, prepared, messages_per_call=messages_per_call, max_retries=max_retries
        )
    elif method == "batch":
        result = _submit_batch(
            client, prepared, batch_metadata=batch_metadata, max_retries=max_retries
        )
    else:
        try:
            result = _submit_batch(
                client, prepared, batch_metadata=batch_metadata, max_retries=max_retries
            )
        except BatchUnavailableError as error:
            partial = error.partial_result
            if partial is not None and partial.batch_ids:
                # Earlier batches were already submitted; re-submitting all
                # messages sequentially would duplicate them. Surface instead.
                raise
            notice = (
                "Zep Batch API not available on this plan — falling back to "
                "sequential thread.add_messages ingestion."
            )
            logger.info(notice)
            result = _submit_sequential(
                client,
                prepared,
                messages_per_call=messages_per_call,
                max_retries=max_retries,
            )
            result.warnings.insert(0, notice)
    if result.method == "sequential":
        result.warnings.append(
            "Sequential thread ingestion has no completion handle: wait()/status "
            "reflect submission only, and extraction continues server-side. Poll "
            "your own read (e.g. zep_ingest.search_when_ready) before querying."
        )
    result.warnings.extend(warnings)
    return result
