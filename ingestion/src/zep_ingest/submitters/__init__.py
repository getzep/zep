"""Submitters and the method="auto" dispatch."""

import logging
from collections.abc import Iterable
from itertools import chain
from typing import Any, Literal

from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.result import IngestResult
from zep_ingest.submitters.batch import BatchSubmitter, is_gating_error
from zep_ingest.submitters.sequential import SequentialSubmitter
from zep_ingest.types import (
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    Destination,
    Episode,
)

logger = logging.getLogger("zep_ingest")

Method = Literal["auto", "batch", "sequential"]

__all__ = ["BatchSubmitter", "Method", "SequentialSubmitter", "submit_episodes"]


def submit_episodes(
    client: Zep,
    episodes: Iterable[Episode],
    destination: Destination,
    *,
    method: Method = "auto",
    page_size: int = MAX_ITEMS_PER_ADD,
    max_items_per_batch: int = MAX_ITEMS_PER_BATCH,
    batch_metadata: dict[str, Any] | None = None,
    max_add_retries: int = 3,
    max_retries: int = 5,
    min_interval: float = 0.0,
) -> IngestResult:
    """Submit an episode stream via the requested method.

    method="auto" uses the Batch API when the plan allows it and falls back to
    sequential graph.add ingestion otherwise; no episodes are lost on fallback.
    """
    if method not in ("auto", "batch", "sequential"):
        raise ConfigurationError(
            f"method must be one of ['auto', 'batch', 'sequential'], got {method!r}"
        )
    require_int_range("page_size", page_size, minimum=1, maximum=MAX_ITEMS_PER_ADD)
    require_int_range(
        "max_items_per_batch",
        max_items_per_batch,
        minimum=page_size,
        maximum=MAX_ITEMS_PER_BATCH,
    )
    require_int_range("max_add_retries", max_add_retries, minimum=1)
    require_int_range("max_retries", max_retries, minimum=1)
    require_nonnegative_number("min_interval", min_interval)

    if method == "sequential":
        return SequentialSubmitter(
            client, max_retries=max_retries, min_interval=min_interval
        ).submit(episodes, destination)

    batch_kwargs: dict[str, Any] = {
        "page_size": page_size,
        "max_items_per_batch": max_items_per_batch,
        "batch_metadata": batch_metadata,
        "max_add_retries": max_add_retries,
    }
    if method == "batch":
        return BatchSubmitter(client, **batch_kwargs).submit(episodes, destination)

    # method == "auto": probe batch availability with the real batch.create,
    # then hand the (untouched) stream to the chosen submitter.
    iterator = iter(episodes)
    try:
        first = next(iterator)
    except StopIteration:
        return IngestResult(method="sequential", client=client)
    stream = chain([first], iterator)
    try:
        if batch_metadata is not None:
            summary = client.batch.create(metadata=batch_metadata)
        else:
            summary = client.batch.create()
    except ApiError as error:
        if is_gating_error(error):
            notice = (
                "Zep Batch API not available on this plan — falling back to "
                "sequential graph.add ingestion."
            )
            logger.info(notice)
            result = SequentialSubmitter(
                client, max_retries=max_retries, min_interval=min_interval
            ).submit(stream, destination)
            result.warnings.insert(0, notice)
            return result
        raise
    return BatchSubmitter(client, initial_batch_id=summary.batch_id, **batch_kwargs).submit(
        stream, destination
    )
