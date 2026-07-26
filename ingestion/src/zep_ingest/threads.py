"""User-data ingestion: chat history backfills into user graphs via threads.

Business data goes to named graphs as episodes; a user's own conversations go
to their user graph as thread messages. This module owns that path end to end:

- validates every message client-side (thread_id, role, name, content,
  created_at, metadata) before any API call,
- requires the user to already exist and pre-creates the destination threads
  (the Batch API requires threads to exist),
- auto-splits messages over the 4,096-character message limit at sentence
  boundaries instead of letting the API reject them,
- submits via the Batch API by default, with transparent fallback to sequential
  ``thread.add_messages`` when batch submission is unavailable for the account
  or deployment — preserving per-thread chronological order either way.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import Any, Literal

import httpx
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.errors.not_found_error import NotFoundError
from zep_cloud.types.batch_add_item import BatchAddItem
from zep_cloud.types.message import Message

from zep_ingest._errors import format_api_error
from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest._validation import (
    check_required_string,
    check_scalar_map,
    check_timestamp,
    require_int_range,
)
from zep_ingest.exceptions import (
    BatchUnavailableError,
    ConfigurationError,
    InvalidBatchResponseError,
)
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.batch import (
    is_batch_unavailable,
    process_batch,
    require_batch_id,
    rollover_failure_message,
)
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.transforms._splitting import split_text
from zep_ingest.types import (
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    MAX_MESSAGES_PER_THREAD_ADD,
    MAX_METADATA_KEYS,
)

logger = logging.getLogger("zep_ingest")

#: Documented per-message content limit for thread messages (thread.add_messages
#: and thread_message batch items alike) — distinct from the 10k episode limit.
MAX_MESSAGE_CHARS = 4096
_SPLIT_TARGET = 4000  # headroom under the hard limit

ROLE_TYPES = frozenset({"user", "assistant", "system", "function", "tool", "norole"})


@dataclass(slots=True)
class ThreadMessage:
    """One chat message destined for a user's thread, validated client-side.

    ``role``, ``name``, and ``created_at`` are all required: a backfill should
    carry each turn's speaker type, speaker name, and original timestamp so the
    conversation and its fact-validity timeline reconstruct faithfully. Only
    ``metadata`` is optional.
    """

    thread_id: str
    content: str
    role: str
    name: str
    created_at: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.thread_id, str) or not self.thread_id.strip():
            errors.append("thread_id must be a non-empty string")
        if not isinstance(self.content, str) or not self.content.strip():
            errors.append("content must be a non-empty string")
        if not isinstance(self.role, str) or self.role not in ROLE_TYPES:
            errors.append(f"role must be one of {sorted(ROLE_TYPES)}, got {self.role!r}")
        check_required_string("name", self.name, 100, errors)
        if self.created_at is None:
            errors.append("created_at is required (RFC3339, e.g. 2024-06-15T10:30:00Z)")
        check_timestamp("created_at", self.created_at, errors)
        check_scalar_map("metadata", self.metadata, errors, max_keys=MAX_METADATA_KEYS)
        if errors:
            raise ConfigurationError(
                f"Invalid thread message (thread {self.thread_id!r}): " + "; ".join(errors)
            )


def _validate_ignore_roles(ignore_roles: Sequence[str] | None) -> list[str] | None:
    """Validate ``ignore_roles`` against the documented role types before any
    API call. Returns an order-preserving, de-duplicated list, or ``None`` when
    unset/empty so the field is simply omitted from the request."""
    if ignore_roles is None:
        return None
    if isinstance(ignore_roles, str):
        raise ConfigurationError(
            "ignore_roles must be a list of roles (e.g. ['assistant']), not a bare string"
        )
    try:
        roles = list(ignore_roles)
    except TypeError:
        raise ConfigurationError(
            "ignore_roles must be a list of role strings (e.g. ['assistant'])"
        ) from None
    invalid = [r for r in roles if r not in ROLE_TYPES]
    if invalid:
        raise ConfigurationError(
            f"ignore_roles contains unknown role(s) {invalid!r}; "
            f"valid roles are {sorted(ROLE_TYPES)}"
        )
    return list(dict.fromkeys(roles)) or None


def _load_messages(path: Path) -> list[ThreadMessage]:
    rows = rows_to_fields(load_rows(path), ThreadMessage)
    return [ThreadMessage(**row) for row in rows]


def _prepare(messages: list[ThreadMessage], warnings: list[str]) -> list[ThreadMessage]:
    """Split messages whose content exceeds the thread-message limit."""
    prepared: list[ThreadMessage] = []
    split_count = 0
    for message in messages:
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
    return prepared


def _ensure_user_and_threads(client: Zep, user_id: str, messages: list[ThreadMessage]) -> None:
    # The user must already exist — ingestion writes into existing users and does
    # not create them (a bare auto-created user would skip the profile and
    # per-user setup that user creation is the place for). Threads, by contrast,
    # are backfill-owned containers, so they are created below if missing.
    try:
        client.user.get(user_id)
    except NotFoundError:
        raise ConfigurationError(
            f"User {user_id!r} does not exist. Create it first — e.g. "
            f"client.user.add(user_id={user_id!r}, ...) — then ingest; ingestion "
            "writes into existing users and does not create them."
        ) from None
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
            # Thread IDs are project-global. A collision must be verified
            # before adding messages; otherwise this import could cross a user
            # boundary and write into another user's conversation.
            existing = client.thread.get(message.thread_id, lastn=1)
            owner_id = getattr(existing, "user_id", None)
            if owner_id != user_id:
                owner = repr(owner_id) if owner_id is not None else "unknown"
                raise ConfigurationError(
                    f"Thread {message.thread_id!r} already belongs to user {owner}; "
                    f"refusing to ingest it for {user_id!r}."
                ) from error


def _submit_batch(
    client: Zep,
    messages: list[ThreadMessage],
    *,
    batch_metadata: dict[str, Any] | None,
    ignore_roles: list[str] | None,
    max_retries: int,
) -> IngestResult:
    result = IngestResult(method="batch", client=client)
    create_kwargs: dict[str, Any] = {}
    if batch_metadata is not None:
        create_kwargs["metadata"] = batch_metadata
    if ignore_roles:
        create_kwargs["ignore_roles"] = ignore_roles
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
            # batch.create is retried on transient errors (429, unsent transport
            # failures) like every other batch call, so a momentary blip can't
            # crash the run or wrongly trip the sequential fallback — only a
            # genuinely absent batch endpoint does that. A transient error that
            # survives retries is re-raised, not silently downgraded.
            summary, create_error = call_with_retries(
                lambda: client.batch.create(**create_kwargs),
                max_retries=max_retries,
            )
            if create_error is not None:
                if result.batch_ids:
                    # A rollover: earlier batches are already processing and their
                    # ids are the only handle on them, so stop and report rather
                    # than raising them away.
                    result.add_errors.append(
                        AddError(
                            index=-1,
                            item_count=0,
                            error=rollover_failure_message(create_error, result),
                        )
                    )
                    break
                if isinstance(create_error, httpx.TransportError):
                    # No response, so the batch may exist without us knowing its id.
                    raise InvalidBatchResponseError(
                        f"{format_api_error('batch.create', create_error)}; refusing to submit "
                        "because the batch may already have been created.",
                        partial_result=result,
                    ) from create_error
                if is_batch_unavailable(create_error):
                    raise BatchUnavailableError(partial_result=result) from create_error
                raise create_error
            batch_id = require_batch_id(
                getattr(summary, "batch_id", None),
                partial_result=result,
            )
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
                    error=(format_api_error("batch.add", add_failure)),
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
    ignore_roles: list[str] | None,
    max_retries: int,
) -> IngestResult:
    result = IngestResult(method="sequential", client=client)
    missing_task_handles = 0
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
            add_kwargs: dict[str, Any] = {"messages": payload}
            if ignore_roles:
                add_kwargs["ignore_roles"] = ignore_roles
            response, error = call_with_retries(
                lambda: client.thread.add_messages(thread_id, **add_kwargs),  # noqa: B023
                max_retries=max_retries,
            )
            if error is not None:
                result.add_errors.append(
                    AddError(
                        index=chunk_index,
                        item_count=len(chunk),
                        error=format_api_error(f"thread.add_messages({thread_id!r})", error),
                    )
                )
            else:
                result.items_submitted += len(chunk)
                task_id = getattr(response, "task_id", None)
                if task_id:
                    normalized_task_id = str(task_id)
                    if normalized_task_id not in result.task_ids:
                        result.task_ids.append(normalized_task_id)
                else:
                    missing_task_handles += 1
                    result.untracked_items += len(chunk)
            chunk_index += 1
    if missing_task_handles:
        result.warnings.append(
            f"{missing_task_handles} successful thread.add_messages call(s) returned no "
            "completion handle; wait()/status cannot track their server-side extraction. "
            "Poll your own read (e.g. zep_ingest.search_when_ready) before querying."
        )
    return result


def ingest_thread_messages(
    client: Zep,
    messages: Iterable[ThreadMessage] | str | Path,
    *,
    user_id: str | None = None,
    method: Literal["auto", "batch", "sequential"] = "auto",
    batch_metadata: dict[str, Any] | None = None,
    ignore_roles: Sequence[str] | None = None,
    messages_per_call: int = MAX_MESSAGES_PER_THREAD_ADD,
    max_retries: int = 5,
    thread_id_suffix: str | None = None,
) -> IngestResult:
    """Backfill chat history into a user's graph via threads.

    Accepts ThreadMessage objects or a JSONL / JSON-object / JSON-array path with columns
    thread_id/role/name/content/created_at (all required except metadata). The
    user must already exist; every referenced thread is created if missing (the
    Batch API requires threads to exist), and per-thread message order is
    preserved on both submission paths.

    Thread ids are global to a Zep project — pass ``thread_id_suffix`` to
    namespace them (e.g. per environment or per re-run) without rewriting
    your source data.

    ``ignore_roles`` lists message roles (e.g. ``["assistant"]``) to keep as
    conversational context but exclude from graph extraction; those messages are
    still stored in thread history. Applies to both the batch and sequential
    submission paths.

    Submission is asynchronous; bind the result, then wait on it, so the resume
    handles survive a timeout::

        result = ingest_thread_messages(client, messages, user_id="u1")
        result.wait(timeout=600)
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
    require_int_range(
        "messages_per_call",
        messages_per_call,
        minimum=1,
        maximum=MAX_MESSAGES_PER_THREAD_ADD,
    )
    require_int_range("max_retries", max_retries, minimum=1)
    if thread_id_suffix is not None and not isinstance(thread_id_suffix, str):
        raise ConfigurationError("thread_id_suffix must be a string or None")
    normalized_ignore_roles = _validate_ignore_roles(ignore_roles)
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

    if method == "sequential":
        result = _submit_sequential(
            client,
            prepared,
            messages_per_call=messages_per_call,
            ignore_roles=normalized_ignore_roles,
            max_retries=max_retries,
        )
    elif method == "batch":
        result = _submit_batch(
            client,
            prepared,
            batch_metadata=batch_metadata,
            ignore_roles=normalized_ignore_roles,
            max_retries=max_retries,
        )
    else:  # auto: prefer the Batch API, fall back to sequential when unavailable
        try:
            result = _submit_batch(
                client,
                prepared,
                batch_metadata=batch_metadata,
                ignore_roles=normalized_ignore_roles,
                max_retries=max_retries,
            )
        except BatchUnavailableError as error:
            partial = error.partial_result
            if partial is not None and partial.batch_ids:
                # Earlier batches were already submitted; re-submitting all
                # messages sequentially would duplicate them. Surface instead.
                raise
            notice = (
                "Zep Batch API not available for this account — falling back to "
                "sequential thread.add_messages ingestion."
            )
            logger.info(notice)
            result = _submit_sequential(
                client,
                prepared,
                messages_per_call=messages_per_call,
                ignore_roles=normalized_ignore_roles,
                max_retries=max_retries,
            )
            result.warnings.insert(0, notice)
    result.warnings.extend(warnings)
    return result
