"""Tests for BatchSubmitter (enterprise Batch API path)."""

from unittest.mock import call

import pytest
from zep_cloud.core.api_error import ApiError

from tests.conftest import make_batch_summary
from zep_ingest.exceptions import BatchUnavailableError
from zep_ingest.submitters.batch import BatchSubmitter
from zep_ingest.types import Destination, Episode


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
        assert result.add_errors[0].error == "batch.add failed: status=500 after 1 attempt(s)"
        assert result.items_submitted == 2

    def test_add_error_does_not_contain_episode_data(self, mock_zep):
        mock_zep.batch.add.side_effect = ApiError(status_code=500, body="server error")
        result = BatchSubmitter(mock_zep, max_add_retries=1).submit(
            [Episode(data="SENSITIVE-CONTENT")], DEST
        )
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


class TestBatchMetadata:
    def test_batch_metadata_passed_to_create(self, mock_zep):
        BatchSubmitter(mock_zep, batch_metadata={"run": "backfill-1"}).submit(episodes(1), DEST)
        assert mock_zep.batch.create.call_args.kwargs["metadata"] == {"run": "backfill-1"}

    def test_rollover_gating_error_carries_partial_result(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            ApiError(status_code=403),
        ]
        with pytest.raises(BatchUnavailableError) as caught:
            BatchSubmitter(mock_zep, page_size=1, max_items_per_batch=1).submit(episodes(2), DEST)
        partial = caught.value.partial_result
        assert partial is not None
        assert partial.batch_ids == ["b1"]
        assert partial.items_submitted == 1
