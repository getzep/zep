"""Tests for IngestResult: status aggregation, refresh, wait, failed_items."""

import pytest

from tests.conftest import make_batch_summary, make_item_detail, make_item_list, make_zep_episode
from zep_ingest.exceptions import (
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    IngestUntrackedError,
)
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

    @pytest.mark.parametrize("field", ["poll_interval", "timeout"])
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_wait_timing_refused_before_polling(self, mock_zep, field, value):
        # A non-finite interval reaches time.sleep as a ValueError (nan) or an
        # OverflowError (inf), and a non-finite timeout is worse than either: it
        # silently stops being a timeout, because `elapsed >= nan` and
        # `elapsed >= inf` are never true, so wait() would poll forever.
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        with pytest.raises(ConfigurationError, match=field):
            result.wait(**{field: value})

        mock_zep.batch.get.assert_not_called()

    def test_finite_timeout_still_fires(self, mock_zep, monkeypatch):
        # the control for the case above: with a clock that leaps past any
        # deadline, a finite timeout terminates the loop on the first check
        clock = iter(float(seconds) for seconds in range(0, 10_000_000, 1_000_000))
        monkeypatch.setattr("zep_ingest.result.time.monotonic", lambda: next(clock))
        monkeypatch.setattr("zep_ingest.result.time.sleep", lambda _: None)
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "processing")

        with pytest.raises(IngestTimeoutError):
            result.wait(poll_interval=0, timeout=30.0)

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

    def test_failed_items_includes_pages_rejected_before_batch_acceptance(self, mock_zep):
        submission_error = AddError(index=0, item_count=2, error="batch.add failed", batch_id="b1")
        server_error = make_item_detail(status="failed", sequence_index=3)
        result = IngestResult(
            method="batch",
            batch_ids=["b1"],
            add_errors=[submission_error],
            client=mock_zep,
        )
        mock_zep.batch.list_items.return_value = make_item_list([server_error])

        assert result.failed_items() == [submission_error, server_error]


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

    def test_submitted_work_without_handles_is_untracked(self, mock_zep):
        result = IngestResult(
            method="sequential",
            items_submitted=2,
            untracked_items=2,
            client=mock_zep,
        )

        assert result.status == "untracked"
        with pytest.raises(IngestUntrackedError, match="2 submitted item"):
            result.wait()
        mock_zep.graph.episode.get.assert_not_called()
        mock_zep.task.get.assert_not_called()

    def test_untracked_work_beats_queued_tracked_work(self, mock_zep):
        result = IngestResult(
            method="sequential",
            items_submitted=2,
            task_ids=["t1"],
            untracked_items=1,
            client=mock_zep,
        )

        assert result.status == "untracked"
        with pytest.raises(IngestUntrackedError):
            result.wait()
        mock_zep.task.get.assert_not_called()

    def test_task_ids_are_polled_until_succeeded(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult(method="sequential", task_ids=["t1"], client=mock_zep)
        assert result.status == "queued"
        mock_zep.task.get.side_effect = [
            GetTaskResponse(task_id="t1", status="processing"),
            GetTaskResponse(task_id="t1", status="succeeded"),
        ]

        result.wait(poll_interval=0)

        assert result.status == "succeeded"
        assert mock_zep.task.get.call_count == 2

    def test_failed_task_makes_result_failed(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1"])
        mock_zep.task.get.return_value = GetTaskResponse(task_id="t1", status="failed")

        result.refresh()

        assert result.status == "failed"


class TestRaiseForStatus:
    def test_raises_on_failed(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "failed")
        result.refresh()
        with pytest.raises(IngestFailedError) as excinfo:
            result.raise_for_status()
        message = str(excinfo.value)
        assert "'failed'" in message
        assert "the failure happened server-side" in message
        assert "0 submission error" not in message

    def test_raises_on_canceled_batch(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "canceled")

        result.wait(poll_interval=0)

        assert result.status == "canceled"
        with pytest.raises(IngestFailedError) as excinfo:
            result.raise_for_status()
        message = str(excinfo.value)
        assert "canceled before all items were processed" in message
        assert "0 submission error" not in message

    def test_raises_on_canceled_task(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1"])
        mock_zep.task.get.return_value = GetTaskResponse(task_id="t1", status="cancelled")

        result.wait(poll_interval=0)

        assert result.status == "canceled"
        with pytest.raises(IngestFailedError, match="canceled before all items were processed"):
            result.raise_for_status()

    def test_untracked_is_not_a_failure(self, mock_zep):
        result = IngestResult(
            method="sequential", items_submitted=1, untracked_items=1, client=mock_zep
        )
        assert result.status == "untracked"
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
