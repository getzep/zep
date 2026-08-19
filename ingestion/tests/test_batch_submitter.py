"""Tests for BatchSubmitter (the Batch API path)."""

from unittest.mock import call

import httpx
import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.types.batch_summary import BatchSummary

from tests.conftest import make_batch_summary
from zep_ingest.exceptions import BatchUnavailableError, InvalidBatchResponseError
from zep_ingest.submitters.batch import BatchSubmitter
from zep_ingest.types import DEFAULT_ITEMS_PER_BATCH, Destination, Episode


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda _: None)


def episodes(n: int) -> list[Episode]:
    return [Episode(data=f"episode {i}") for i in range(n)]


DEST = Destination(graph_id="g1")


class TestPaging:
    def test_351_episodes_two_add_calls(self, mock_zep):
        result = BatchSubmitter(mock_zep).submit(episodes(351), DEST)
        mock_zep.batch.create.assert_called_once()
        assert mock_zep.batch.add.call_count == 2
        first_items = mock_zep.batch.add.call_args_list[0].kwargs["items"]
        second_items = mock_zep.batch.add.call_args_list[1].kwargs["items"]
        assert len(first_items) == 350
        assert len(second_items) == 1
        mock_zep.batch.process.assert_called_once_with("batch-1")
        assert result.method == "batch"
        assert result.items_submitted == 351
        assert result.batch_ids == ["batch-1"]

    def test_item_payload_mapping(self, mock_zep):
        ep = Episode(data="hello", data_type="message", created_at="2024-01-01T00:00:00Z")
        BatchSubmitter(mock_zep).submit([ep], DEST)
        [item] = mock_zep.batch.add.call_args.kwargs["items"]
        assert item.data == "hello"
        assert item.data_type == "message"
        assert item.created_at == "2024-01-01T00:00:00Z"
        assert item.graph_id == "g1"
        assert item.type == "graph_episode"

    def test_stream_order_preserved(self, mock_zep):
        BatchSubmitter(mock_zep, page_size=2).submit(episodes(5), DEST)
        seen = [item.data for c in mock_zep.batch.add.call_args_list for item in c.kwargs["items"]]
        assert seen == [f"episode {i}" for i in range(5)]

    def test_empty_stream_no_api_calls(self, mock_zep):
        result = BatchSubmitter(mock_zep).submit([], DEST)
        mock_zep.batch.create.assert_not_called()
        mock_zep.batch.add.assert_not_called()
        assert result.items_submitted == 0
        assert result.status == "succeeded"

    def test_default_max_items_per_batch_is_ten_thousand(self, mock_zep):
        submitter = BatchSubmitter(mock_zep)
        assert submitter.max_items_per_batch == DEFAULT_ITEMS_PER_BATCH == 10_000

    def test_rollover_at_max_items_per_batch(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            make_batch_summary("b2", "draft"),
        ]
        result = BatchSubmitter(mock_zep, page_size=2, max_items_per_batch=4).submit(
            episodes(6), DEST
        )
        assert mock_zep.batch.create.call_count == 2
        assert mock_zep.batch.process.call_args_list == [call("b1"), call("b2")]
        assert result.batch_ids == ["b1", "b2"]
        assert result.items_submitted == 6

    @pytest.mark.parametrize("batch_id", [None, "", "   "])
    def test_missing_created_batch_id_fails_before_add(self, mock_zep, batch_id):
        mock_zep.batch.create.return_value = BatchSummary(batch_id=batch_id, status="draft")

        with pytest.raises(InvalidBatchResponseError, match="batch_id"):
            BatchSubmitter(mock_zep).submit(episodes(1), DEST)

        mock_zep.batch.add.assert_not_called()
        mock_zep.batch.process.assert_not_called()

    @pytest.mark.parametrize("batch_id", ["", "   "])
    def test_blank_initial_batch_id_is_rejected(self, mock_zep, batch_id):
        with pytest.raises(InvalidBatchResponseError, match="initial_batch_id"):
            BatchSubmitter(mock_zep, initial_batch_id=batch_id)

        mock_zep.batch.create.assert_not_called()


class TestRetries:
    def test_rate_limited_add_is_retried(self, mock_zep):
        mock_zep.batch.add.side_effect = [ApiError(status_code=429), None]
        result = BatchSubmitter(mock_zep).submit(episodes(3), DEST)
        assert mock_zep.batch.add.call_count == 2
        assert result.add_errors == []
        assert result.items_submitted == 3

    def test_server_error_add_is_not_retried(self, mock_zep):
        mock_zep.batch.add.side_effect = [ApiError(status_code=500), None]
        result = BatchSubmitter(mock_zep).submit(episodes(3), DEST)
        assert mock_zep.batch.add.call_count == 1
        [error] = result.add_errors
        assert error.error.endswith("after 1 attempt(s)")

    def test_server_error_records_error_and_continues(self, mock_zep):
        def add_side_effect(batch_id, *, items):
            if any(i.data == "episode 0" for i in items):
                raise ApiError(status_code=500, body="boom")
            return None

        mock_zep.batch.add.side_effect = add_side_effect
        result = BatchSubmitter(mock_zep, page_size=1, max_add_retries=2).submit(episodes(3), DEST)
        assert mock_zep.batch.add.call_count == 3  # no ambiguous 5xx retry; one try per page
        assert len(result.add_errors) == 1
        assert result.add_errors[0].index == 0
        assert result.add_errors[0].item_count == 1
        # the server's own message passes through, as a direct SDK call would report it
        assert (
            result.add_errors[0].error
            == "batch.add failed: status=500, body=boom after 1 attempt(s)"
        )
        assert result.items_submitted == 2

    def test_add_error_reports_the_server_message_and_adds_nothing(self, mock_zep):
        """The package reports a failure as the API reported it: the server's body
        passes through, and the package never adds episode content of its own."""
        mock_zep.batch.add.side_effect = ApiError(status_code=500, body="server error")
        result = BatchSubmitter(mock_zep, max_add_retries=1).submit(
            [Episode(data="SENSITIVE-CONTENT")], DEST
        )
        assert "server error" in result.add_errors[0].error
        assert "SENSITIVE-CONTENT" not in result.add_errors[0].error

    def test_transient_process_failure_retried(self, mock_zep):
        mock_zep.batch.process.side_effect = [
            ApiError(status_code=500),
            make_batch_summary("batch-1", "queued"),
        ]
        result = BatchSubmitter(mock_zep).submit(episodes(2), DEST)
        assert mock_zep.batch.process.call_count == 2
        assert result.add_errors == []
        assert result.items_submitted == 2

    def test_exhausted_process_retries_record_error_without_raising(self, mock_zep):
        mock_zep.batch.process.side_effect = ApiError(status_code=500, body="boom")
        result = BatchSubmitter(mock_zep).submit(episodes(2), DEST)
        assert result.items_submitted == 2
        assert result.batch_ids == ["batch-1"]
        [error] = result.add_errors
        assert error.batch_id == "batch-1"
        assert "process" in error.error
        assert result.status == "failed"
        # a failed-to-process batch is terminal: wait() must not hang on it
        assert result.wait(poll_interval=0) is result


class TestTransportErrors:
    """The SDK raises httpx errors untouched and never retries them itself."""

    def test_transport_error_on_add_still_processes_and_returns_batch_id(self, mock_zep):
        calls = {"n": 0}

        def add_side_effect(batch_id, *, items):
            calls["n"] += 1
            if calls["n"] == 2:
                raise httpx.ReadTimeout("response never arrived")

        mock_zep.batch.add.side_effect = add_side_effect
        result = BatchSubmitter(mock_zep, page_size=1).submit(episodes(3), DEST)
        # the batch is processed and its id reaches the caller, so the items
        # that did land are never stranded in an unprocessed draft
        assert result.batch_ids == ["batch-1"]
        mock_zep.batch.process.assert_called_once_with("batch-1")
        assert result.items_submitted == 2
        [error] = result.add_errors
        assert error.index == 1
        assert error.batch_id == "batch-1"
        assert error.error == "batch.add failed: transport error ReadTimeout after 1 attempt(s)"

    def test_connect_error_on_add_is_retried(self, mock_zep):
        mock_zep.batch.add.side_effect = [httpx.ConnectError("connection refused"), None]
        result = BatchSubmitter(mock_zep).submit(episodes(3), DEST)
        assert mock_zep.batch.add.call_count == 2
        assert result.add_errors == []
        assert result.items_submitted == 3

    def test_transport_error_on_process_is_retried(self, mock_zep):
        # processing a known batch is idempotent, so even an ambiguous
        # transport failure is safe to retry
        mock_zep.batch.process.side_effect = [
            httpx.ReadTimeout("response never arrived"),
            make_batch_summary("batch-1", "queued"),
        ]
        result = BatchSubmitter(mock_zep).submit(episodes(2), DEST)
        assert mock_zep.batch.process.call_count == 2
        assert result.add_errors == []

    def test_exhausted_process_transport_retries_record_error_without_raising(self, mock_zep):
        mock_zep.batch.process.side_effect = httpx.ConnectError("connection refused")
        result = BatchSubmitter(mock_zep).submit(episodes(2), DEST)
        assert result.batch_ids == ["batch-1"]
        assert result.items_submitted == 2
        [error] = result.add_errors
        assert error.batch_id == "batch-1"
        assert "transport error ConnectError" in error.error

    def test_transport_error_on_rollover_stops_and_keeps_batch_ids(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            httpx.ConnectError("connection refused"),
        ]

        # max_add_retries=1 pins this to a single attempt: a ConnectError is an
        # unsent failure and so is normally retried, which is covered separately.
        result = BatchSubmitter(
            mock_zep, page_size=1, max_items_per_batch=1, max_add_retries=1
        ).submit(episodes(2), DEST)

        assert result.batch_ids == ["b1"]
        assert result.items_submitted == 1
        assert result.add_errors[-1].index == -1
        assert "transport error ConnectError" in result.add_errors[-1].error
        # a transport failure leaves it unknowable whether a batch was opened
        assert "without its id being returned" in result.add_errors[-1].error

    def test_transient_error_on_rollover_create_is_retried(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            ApiError(status_code=429),
            make_batch_summary("b2", "draft"),
        ]

        result = BatchSubmitter(mock_zep, page_size=1, max_items_per_batch=1).submit(
            episodes(2), DEST
        )

        assert result.batch_ids == ["b1", "b2"]  # the blip did not end the run
        assert result.items_submitted == 2
        assert result.add_errors == []


class TestBatchMetadata:
    def test_batch_metadata_passed_to_create(self, mock_zep):
        BatchSubmitter(mock_zep, batch_metadata={"run": "backfill-1"}).submit(episodes(1), DEST)
        assert mock_zep.batch.create.call_args.kwargs["metadata"] == {"run": "backfill-1"}

    def test_first_batch_failure_still_raises(self, mock_zep):
        """The rollover leniency applies only once work is in flight. On the very
        first batch nothing has been submitted, so the caller must see the failure
        — that is what lets the dispatcher fall back to sequential."""
        mock_zep.batch.create.side_effect = ApiError(status_code=404)
        with pytest.raises(BatchUnavailableError):
            BatchSubmitter(mock_zep, page_size=1, max_items_per_batch=1).submit(episodes(2), DEST)

    @pytest.mark.parametrize("status_code", [402, 403, 404, 500])
    def test_rollover_failure_stops_the_run_without_losing_batch_ids(self, mock_zep, status_code):
        """Whatever the cause, a mid-run create failure must not raise: the batches
        already submitted are still processing, and their ids are the only handle
        on them. The reason is recorded on the result instead."""
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            ApiError(status_code=status_code, body="refused"),
        ]

        result = BatchSubmitter(mock_zep, page_size=1, max_items_per_batch=1).submit(
            episodes(2), DEST
        )

        assert result.batch_ids == ["b1"]
        assert result.items_submitted == 1  # the second episode never went out
        assert result.add_errors[-1].index == -1
        assert result.add_errors[-1].item_count == 0
        assert f"status={status_code}" in result.add_errors[-1].error
        assert "body=refused" in result.add_errors[-1].error
        assert "result.batch_ids" in result.add_errors[-1].error
        assert result.status == "partial"

    def test_rollover_without_batch_id_carries_partial_result(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            BatchSummary(status="draft"),
        ]

        with pytest.raises(InvalidBatchResponseError) as caught:
            BatchSubmitter(mock_zep, page_size=1, max_items_per_batch=1).submit(episodes(2), DEST)

        partial = caught.value.partial_result
        assert partial is not None
        assert partial.batch_ids == ["b1"]
        assert partial.items_submitted == 1
        assert mock_zep.batch.add.call_count == 1
