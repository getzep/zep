"""Tests for IngestResult: status aggregation, refresh, wait, failed_items."""

import pytest

from tests.conftest import make_batch_summary, make_item_detail, make_item_list, make_zep_episode
from zep_ingest.exceptions import IngestFailedError, IngestTimeoutError
from zep_ingest.result import AddError, IngestResult


class TestBatchResult:
    def test_status_aggregation_priority(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "succeeded"),
            make_batch_summary("b2", "failed"),
        ]
        result.refresh()
        assert result.status == "failed"

    def test_partial_beats_processing(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "partial"),
            make_batch_summary("b2", "processing"),
        ]
        result.refresh()
        assert result.status == "partial"

    def test_all_succeeded(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "succeeded")
        result.refresh()
        assert result.status == "succeeded"

    def test_status_without_refresh_is_queued(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        assert result.status == "queued"

    def test_from_batch_ids_reconstructs_result(self, mock_zep):
        result = IngestResult.from_batch_ids(mock_zep, ["b1", "b2"])
        assert result.method == "batch"
        assert result.batch_ids == ["b1", "b2"]
        assert result.status == "queued"
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "succeeded"),
            make_batch_summary("b2", "processing"),
        ]
        result.refresh()
        assert result.status == "processing"

    def test_wait_polls_until_terminal(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "processing"),
            make_batch_summary("b1", "processing"),
            make_batch_summary("b1", "succeeded"),
        ]
        returned = result.wait(poll_interval=0)
        assert returned is result
        assert result.status == "succeeded"
        assert mock_zep.batch.get.call_count == 3

    def test_wait_timeout_raises_but_result_usable(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "processing")
        with pytest.raises(IngestTimeoutError):
            result.wait(poll_interval=0, timeout=0)
        assert result.status == "processing"

    def test_failed_items_pages_across_batches(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.list_items.side_effect = [
            make_item_list([make_item_detail(status="failed", sequence_index=3)], next_cursor=7),
            make_item_list([make_item_detail(status="failed", sequence_index=9)]),
            make_item_list([make_item_detail(status="failed", sequence_index=1)]),
        ]
        items = result.failed_items()
        assert len(items) == 3
        first_call = mock_zep.batch.list_items.call_args_list[0]
        assert first_call.args == ("b1",)
        assert first_call.kwargs["status"] == "failed"
        second_call = mock_zep.batch.list_items.call_args_list[1]
        assert second_call.kwargs["cursor"] == 7

    def test_failed_items_respects_limit(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.list_items.return_value = make_item_list(
            [make_item_detail(status="failed", sequence_index=i) for i in range(5)]
        )
        assert len(result.failed_items(limit=2)) == 2


class TestSequentialResult:
    def test_status_processing_until_all_processed(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=False),
        ]
        result.refresh()
        assert result.status == "processing"

    def test_status_succeeded_when_all_processed(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=True),
        ]
        result.refresh()
        assert result.status == "succeeded"

    def test_refresh_skips_already_processed_uuids(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=False),
            make_zep_episode("e2", processed=True),
        ]
        result.refresh()
        result.refresh()
        assert result.status == "succeeded"
        assert mock_zep.graph.episode.get.call_count == 3

    def test_add_errors_make_status_partial(self, mock_zep):
        result = IngestResult(
            method="sequential",
            episode_uuids=["e1"],
            add_errors=[AddError(index=5, item_count=1, error="boom")],
            client=mock_zep,
        )
        mock_zep.graph.episode.get.return_value = make_zep_episode("e1", processed=True)
        result.refresh()
        assert result.status == "partial"

    def test_failed_items_returns_add_errors(self, mock_zep):
        errors = [AddError(index=0, item_count=1, error="x")]
        result = IngestResult(
            method="sequential", episode_uuids=[], add_errors=errors, client=mock_zep
        )
        assert result.failed_items() == errors

    def test_empty_run_is_succeeded(self, mock_zep):
        result = IngestResult(method="sequential", client=mock_zep)
        assert result.status == "succeeded"


class TestRaiseForStatus:
    def test_raises_on_failed(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "failed")
        result.refresh()
        with pytest.raises(IngestFailedError):
            result.raise_for_status()

    def test_raises_on_add_errors(self, mock_zep):
        result = IngestResult(
            method="sequential",
            add_errors=[AddError(index=0, item_count=1, error="x")],
            client=mock_zep,
        )
        with pytest.raises(IngestFailedError):
            result.raise_for_status()

    def test_no_raise_when_clean(self, mock_zep):
        result = IngestResult(method="sequential", client=mock_zep)
        result.raise_for_status()

    def test_add_error_never_contains_episode_body(self):
        err = AddError(index=1, item_count=1, error="API says no")
        assert not hasattr(err, "data")
        assert not hasattr(err, "episode")
