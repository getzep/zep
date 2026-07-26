"""BatchSubmitter: bulk submission via the Zep Batch API.

Pages the episode stream at the API's 350-items-per-add limit and rolls over to
a new batch at the 50k-items-per-batch limit. Nothing that happens after the
first batch opens is allowed to crash the run: a page that keeps failing is
recorded as an AddError and the run continues, and a rollover that cannot open
its next batch stops the run with the reason recorded — because the batches
already submitted are still processing, and their ids are the only handle on
them.
"""

from collections.abc import Iterable
from itertools import islice
from typing import Any

import httpx
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

from zep_ingest._errors import SubmitError, format_api_error
from zep_ingest._validation import require_int_range
from zep_ingest.exceptions import BatchUnavailableError, InvalidBatchResponseError
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.types import (
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    Destination,
    Episode,
    to_batch_item,
)

#: The only status that means "this deployment does not serve the Batch API",
#: and so the only failure sequential ingestion can work around. Every other
#: refusal — a rejected key, an exhausted quota — would refuse graph.add just
#: as readily, so falling back would only hide the real error behind a slow run.
BATCH_UNAVAILABLE_STATUS_CODES = frozenset({404})


def require_batch_id(
    batch_id: Any,
    *,
    source: str = "batch.create",
    partial_result: IngestResult | None = None,
) -> str:
    """Return a usable batch ID or fail before any add/process call."""
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise InvalidBatchResponseError(
            f"{source} returned no usable batch_id; refusing to submit because "
            "the batch may already have been created.",
            partial_result=partial_result,
        )
    return batch_id


def rollover_failure_message(error: SubmitError, result: IngestResult) -> str:
    """Explain a failed mid-run batch.create in terms of what is still in flight."""
    unknown_batch = (
        " A further batch may have been created without its id being returned."
        if isinstance(error, httpx.TransportError)
        else ""
    )
    return (
        f"{format_api_error('batch.create', error)}; stopped after "
        f"{len(result.batch_ids)} batch(es). Those batches are still processing — see "
        f"result.batch_ids. The remaining items were not submitted.{unknown_batch}"
    )


def is_batch_unavailable(error: SubmitError) -> bool:
    # A transport error carries no status, so it can never prove unavailability.
    return isinstance(error, ApiError) and error.status_code in BATCH_UNAVAILABLE_STATUS_CODES


def process_batch(client: Zep, batch_id: str, result: IngestResult, *, max_retries: int) -> None:
    """Trigger batch processing, retrying transient errors. A persistent failure
    is recorded on the result (and the batch pinned terminal) instead of raising,
    so a filled batch's ids and errors are never lost mid-run."""
    _, error = call_with_retries(
        lambda: client.batch.process(batch_id),
        max_retries=max_retries,
        # Processing a known batch is idempotent; retrying it cannot add items
        # twice, unlike graph.add or batch.add.
        retry_server_errors=True,
    )
    if error is not None:
        result.mark_batch_failed(
            batch_id,
            f"{format_api_error('batch.process', error)}; "
            f"items were added but the batch was never processed — retry with "
            f"client.batch.process({batch_id!r}).",
        )


class BatchSubmitter:
    def __init__(
        self,
        client: Zep,
        *,
        page_size: int = MAX_ITEMS_PER_ADD,
        max_items_per_batch: int = MAX_ITEMS_PER_BATCH,
        batch_metadata: dict[str, Any] | None = None,
        max_add_retries: int = 3,
        initial_batch_id: str | None = None,
    ) -> None:
        require_int_range("page_size", page_size, minimum=1, maximum=MAX_ITEMS_PER_ADD)
        require_int_range(
            "max_items_per_batch",
            max_items_per_batch,
            minimum=page_size,
            maximum=MAX_ITEMS_PER_BATCH,
        )
        require_int_range("max_add_retries", max_add_retries, minimum=1)
        self.client = client
        self.page_size = page_size
        self.max_items_per_batch = max_items_per_batch
        self.batch_metadata = batch_metadata
        self.max_add_retries = max_add_retries
        self.initial_batch_id = (
            require_batch_id(initial_batch_id, source="initial_batch_id")
            if initial_batch_id is not None
            else None
        )

    def submit(self, episodes: Iterable[Episode], destination: Destination) -> IngestResult:
        result = IngestResult(method="batch", client=self.client)
        iterator = iter(episodes)
        batch_id: str | None = None
        items_in_batch = 0
        page_index = 0
        while True:
            page = list(islice(iterator, self.page_size))
            if not page:
                break
            if batch_id is not None and items_in_batch + len(page) > self.max_items_per_batch:
                process_batch(self.client, batch_id, result, max_retries=self.max_add_retries)
                batch_id = None
            if batch_id is None:
                batch_id = self._create_batch(result)
                if batch_id is None:
                    break
                items_in_batch = 0
            items = [to_batch_item(ep, destination) for ep in page]
            if self._add_page(batch_id, items, page_index, result):
                items_in_batch += len(page)
                result.items_submitted += len(page)
            page_index += 1
        if batch_id is not None:
            process_batch(self.client, batch_id, result, max_retries=self.max_add_retries)
        return result

    def _create_batch(self, result: IngestResult) -> str | None:
        """Open the next batch, or return None when a rollover cannot continue.

        A failure opening the *first* batch is raised: nothing has been
        submitted yet, and the caller may still fall back to sequential. A
        failure on a rollover is recorded instead — earlier batches are already
        processing, and their ids are the only handle on them, so raising would
        throw away the run's only means of recovery.
        """
        if self.initial_batch_id is not None:
            batch_id = self.initial_batch_id
            self.initial_batch_id = None
            result.batch_ids.append(batch_id)
            return batch_id

        def create() -> Any:
            if self.batch_metadata is not None:
                return self.client.batch.create(metadata=self.batch_metadata)
            return self.client.batch.create()

        # Retried on transient errors like every other batch call, so a
        # momentary blip cannot end the run early.
        summary, error = call_with_retries(create, max_retries=self.max_add_retries)
        if error is not None:
            if result.batch_ids:
                result.add_errors.append(
                    AddError(
                        index=-1,
                        item_count=0,
                        error=rollover_failure_message(error, result),
                    )
                )
                return None
            if isinstance(error, httpx.TransportError):
                # No response, so the batch may exist without us knowing its id.
                raise InvalidBatchResponseError(
                    f"{format_api_error('batch.create', error)}; refusing to submit because "
                    "the batch may already have been created.",
                    partial_result=result,
                ) from error
            if is_batch_unavailable(error):
                raise BatchUnavailableError(partial_result=result) from error
            raise error
        batch_id = require_batch_id(
            getattr(summary, "batch_id", None),
            partial_result=result,
        )
        result.batch_ids.append(batch_id)
        return batch_id

    def _add_page(
        self, batch_id: str, items: list[Any], page_index: int, result: IngestResult
    ) -> bool:
        attempts = 0

        def add_page() -> None:
            nonlocal attempts
            attempts += 1
            self.client.batch.add(batch_id, items=items)

        _, error = call_with_retries(
            add_page,
            max_retries=self.max_add_retries,
        )
        if error is None:
            return True
        result.add_errors.append(
            AddError(
                index=page_index,
                item_count=len(items),
                error=f"{format_api_error('batch.add', error)} after {attempts} attempt(s)",
                batch_id=batch_id,
            )
        )
        return False
