"""BatchSubmitter: bulk submission via the enterprise Zep Batch API.

Pages the episode stream at the API's 350-items-per-add limit, rolls over to a
new batch at the 50k-items-per-batch limit, and never crashes mid-run: a page
that keeps failing is recorded as an AddError and the run continues.
"""

import time
from collections.abc import Iterable
from itertools import islice
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

from zep_ingest._errors import safe_api_error
from zep_ingest._validation import require_int_range
from zep_ingest.exceptions import BatchUnavailableError
from zep_ingest.result import AddError, IngestResult
from zep_ingest.submitters.sequential import call_with_retries
from zep_ingest.types import (
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    Destination,
    Episode,
    to_batch_item,
)

#: Statuses the Batch API returns when the feature is not enabled for the plan.
GATING_STATUS_CODES = frozenset({402, 403, 404})


def is_gating_error(error: ApiError) -> bool:
    return error.status_code in GATING_STATUS_CODES


def process_batch(client: Zep, batch_id: str, result: IngestResult, *, max_retries: int) -> None:
    """Trigger batch processing, retrying transient errors. A persistent failure
    is recorded on the result (and the batch pinned terminal) instead of raising,
    so a filled batch's ids and errors are never lost mid-run."""
    _, error = call_with_retries(lambda: client.batch.process(batch_id), max_retries=max_retries)
    if error is not None:
        result.mark_batch_failed(
            batch_id,
            f"{safe_api_error('batch.process', error)}; "
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
        self.initial_batch_id = initial_batch_id

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
                items_in_batch = 0
            items = [to_batch_item(ep, destination) for ep in page]
            if self._add_page(batch_id, items, page_index, result):
                items_in_batch += len(page)
                result.items_submitted += len(page)
            page_index += 1
        if batch_id is not None:
            process_batch(self.client, batch_id, result, max_retries=self.max_add_retries)
        return result

    def _create_batch(self, result: IngestResult) -> str:
        try:
            if self.initial_batch_id is not None:
                batch_id = self.initial_batch_id
                self.initial_batch_id = None
            else:
                if self.batch_metadata is not None:
                    summary = self.client.batch.create(metadata=self.batch_metadata)
                else:
                    summary = self.client.batch.create()
                batch_id = summary.batch_id or ""
        except ApiError as error:
            if is_gating_error(error):
                raise BatchUnavailableError() from error
            raise
        result.batch_ids.append(batch_id)
        return batch_id

    def _add_page(
        self, batch_id: str, items: list[Any], page_index: int, result: IngestResult
    ) -> bool:
        last_error: ApiError | None = None
        for attempt in range(1, self.max_add_retries + 1):
            try:
                self.client.batch.add(batch_id, items=items)
                return True
            except ApiError as error:
                last_error = error
                if attempt < self.max_add_retries:
                    time.sleep(2 ** (attempt - 1))
        result.add_errors.append(
            AddError(
                index=page_index,
                item_count=len(items),
                error=(
                    f"{safe_api_error('batch.add', last_error) if last_error else 'batch.add failed'} "
                    f"after {self.max_add_retries} attempt(s)"
                ),
                batch_id=batch_id,
            )
        )
        return False
