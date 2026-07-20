"""SequentialSubmitter: one graph.add per episode — works on every Zep plan.

Rate-limit aware: honors the Retry-After header on 429s and otherwise backs
off exponentially with jitter. One call at a time also preserves stream order,
which correct valid_at sequencing depends on.
"""

import random
import time
from collections.abc import Callable, Iterable
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

from zep_ingest._errors import safe_api_error
from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.result import AddError, IngestResult
from zep_ingest.types import Destination, Episode, to_graph_add_kwargs


def _retry_after_seconds(error: ApiError) -> float | None:
    for key, value in (error.headers or {}).items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _is_retryable(error: ApiError) -> bool:
    return error.status_code == 429 or (error.status_code or 0) >= 500


def call_with_retries(
    fn: Callable[[], Any], *, max_retries: int = 5
) -> tuple[Any, ApiError | None]:
    """Call ``fn``, retrying 429/5xx with Retry-After / exponential backoff + jitter.

    Returns (result, None) on success or (None, last_error) once retries are
    exhausted or the error is not retryable.
    """
    require_int_range("max_retries", max_retries, minimum=1)
    last_error: ApiError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn(), None
        except ApiError as error:
            last_error = error
            if not _is_retryable(error) or attempt >= max_retries:
                break
            wait = _retry_after_seconds(error)
            if wait is None:
                wait = (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            time.sleep(wait)
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
                    error=safe_api_error("graph.add", error),
                )
            )
            return
        result.episode_uuids.append(zep_episode.uuid_)
        result.items_submitted += 1
        if self.min_interval > 0:
            time.sleep(self.min_interval)
