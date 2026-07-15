"""Tests for SequentialSubmitter (graph.add path — works on every plan)."""

import pytest
from zep_cloud.core.api_error import ApiError

from tests.conftest import make_zep_episode
from zep_ingest.submitters.sequential import SequentialSubmitter
from zep_ingest.types import Destination, Episode

DEST = Destination(user_id="u1")


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda s: calls.append(s))
    return calls


def episodes(n: int) -> list[Episode]:
    return [Episode(data=f"episode {i}") for i in range(n)]


class TestSubmission:
    def test_order_and_payload(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [make_zep_episode(f"uuid-{i}") for i in range(3)]
        result = SequentialSubmitter(mock_zep).submit(episodes(3), DEST)
        datas = [c.kwargs["data"] for c in mock_zep.graph.add.call_args_list]
        assert datas == ["episode 0", "episode 1", "episode 2"]
        assert all(c.kwargs["user_id"] == "u1" for c in mock_zep.graph.add.call_args_list)
        assert result.method == "sequential"
        assert result.items_submitted == 3
        assert result.episode_uuids == ["uuid-0", "uuid-1", "uuid-2"]

    def test_empty_stream_no_calls(self, mock_zep, sleeps):
        result = SequentialSubmitter(mock_zep).submit([], DEST)
        mock_zep.graph.add.assert_not_called()
        assert result.status == "succeeded"


class TestRateLimits:
    def test_429_honors_retry_after_then_succeeds(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=429, headers={"Retry-After": "3"}),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert result.add_errors == []
        assert result.episode_uuids == ["uuid-0"]
        assert sleeps[0] == 3.0

    def test_429_without_header_uses_backoff(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=429),
            ApiError(status_code=429),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert result.add_errors == []
        assert len(sleeps) == 2
        assert all(s > 0 for s in sleeps)
        # exponential: second wait is longer than the first
        assert sleeps[1] > sleeps[0]

    def test_exhausted_retries_record_error_and_continue(self, mock_zep, sleeps):
        def side_effect(**kwargs):
            if kwargs["data"] == "episode 0":
                raise ApiError(status_code=429, body="rate limited")
            return make_zep_episode(kwargs["data"])

        mock_zep.graph.add.side_effect = side_effect
        result = SequentialSubmitter(mock_zep, max_retries=2).submit(episodes(2), DEST)
        assert len(result.add_errors) == 1
        assert result.add_errors[0].index == 0
        assert result.items_submitted == 1

    def test_client_error_not_retried(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=400, body="bad request"),
            make_zep_episode("uuid-1"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(2), DEST)
        assert mock_zep.graph.add.call_count == 2
        assert len(result.add_errors) == 1
        assert result.add_errors[0].error == "graph.add failed: status=400"
        assert sleeps == []

    def test_server_error_retried(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=503),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert result.add_errors == []
        assert mock_zep.graph.add.call_count == 2
