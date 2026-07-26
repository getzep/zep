"""SequentialSubmitter: one graph.add per episode.

Rate-limit aware: honors the Retry-After header on 429s and otherwise backs
off exponentially with jitter. One call at a time also preserves stream order,
which correct valid_at sequencing depends on.
"""

import math
import random
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

from zep_ingest._errors import SubmitError, format_api_error
from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.result import AddError, IngestResult
from zep_ingest.types import Destination, Episode, to_graph_add_kwargs

#: Ceiling on any single retry sleep. A server or proxy is free to send
#: "Retry-After: 86400"; honoring that verbatim stalls the whole import.
MAX_RETRY_WAIT_SECONDS = 60.0

#: Transport failures raised before the request reached the server: no write can
#: have been applied, so retrying carries no duplication risk — the same
#: reasoning that makes a 429 safe to retry.
_UNSENT_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
)


def _retry_after_seconds(error: ApiError) -> float | None:
    for key, value in (error.headers or {}).items():
        if key.lower() == "retry-after":
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(str(value))
                except (TypeError, ValueError, IndexError):
                    return None
                if retry_at.tzinfo is None:
                    return None
                return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
            # "Retry-After: nan" parses as a float but carries no delay to honor,
            # so treat it as a missing header and let the caller back off instead.
            if not math.isfinite(seconds):
                return None
            # A negative delay means "now", the same as an already-elapsed date.
            return max(0.0, seconds)
    return None


def _is_retryable(error: SubmitError, *, retry_server_errors: bool) -> bool:
    """Return whether retrying is safe for this operation."""
    if isinstance(error, httpx.TransportError):
        return isinstance(error, _UNSENT_TRANSPORT_ERRORS) or retry_server_errors
    return error.status_code == 429 or (retry_server_errors and (error.status_code or 0) >= 500)


def call_with_retries(
    fn: Callable[[], Any], *, max_retries: int = 5, retry_server_errors: bool = False
) -> tuple[Any, SubmitError | None]:
    """Call ``fn`` with rate-limit retries and optional server-error retries.

    A 5xx can mean a non-idempotent write succeeded but its response was lost,
    so those errors are not retried unless the caller establishes idempotency.
    429 responses are safe to retry because the request was rejected before it
    could be processed. Transport errors — which the SDK never converts to an
    ApiError, and never retries itself — are classified on the same axis: one
    raised before the request went out is retried like a 429, one raised after
    it went out (a read timeout, a dropped response) like a 5xx.

    Returns (result, None) on success or (None, last_error) once retries are
    exhausted or the error is not retryable.
    """
    require_int_range("max_retries", max_retries, minimum=1)
    last_error: SubmitError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(), None
        except (ApiError, httpx.TransportError) as error:
            last_error = error
            if (
                not _is_retryable(error, retry_server_errors=retry_server_errors)
                or attempt >= max_retries
            ):
                break
            wait = _retry_after_seconds(error) if isinstance(error, ApiError) else None
            if wait is None:
                wait = (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
    return None, last_error


class SequentialSubmitter:
    def __init__(self, client: Zep, *, max_retries: int = 5, min_interval: float = 0.0) -> None:
        require_int_range("max_retries", max_retries, minimum=1)
        require_nonnegative_number("min_interval", min_interval)
        self.client = client
        self.max_retries = max_retries
        self.min_interval = min_interval

    def submit(self, episodes: Iterable[Episode], destination: Destination) -> IngestResult:
        result = IngestResult(method="sequential", client=self.client)
        for index, episode in enumerate(episodes):
            kwargs = to_graph_add_kwargs(episode, destination)
            self._add_episode(index, kwargs, result)
        return result

    def _add_episode(self, index: int, kwargs: dict, result: IngestResult) -> None:
        zep_episode, error = call_with_retries(
            lambda: self.client.graph.add(**kwargs), max_retries=self.max_retries
        )
        if error is not None:
            result.add_errors.append(
                AddError(
                    index=index,
                    item_count=1,
                    error=format_api_error("graph.add", error),
                )
            )
            return
        result.episode_uuids.append(zep_episode.uuid_)
        result.items_submitted += 1
        if self.min_interval > 0:
            time.sleep(self.min_interval)
