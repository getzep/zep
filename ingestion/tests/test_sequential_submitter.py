"""Tests for SequentialSubmitter (the graph.add path)."""

import httpx
import pytest
from zep_cloud.core.api_error import ApiError

from tests.conftest import make_zep_episode
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.submitters.sequential import (
    MAX_RETRY_WAIT_SECONDS,
    SequentialSubmitter,
    _retry_after_seconds,
    call_with_retries,
)
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


class TestConstructorValidation:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_min_interval_refused(self, mock_zep, value):
        # a non-finite pacing interval reaches time.sleep as a ValueError (nan)
        # or an OverflowError (inf); refuse it while naming the parameter
        with pytest.raises(ConfigurationError, match="min_interval"):
            SequentialSubmitter(mock_zep, min_interval=value)

    def test_finite_min_interval_accepted(self, mock_zep):
        assert SequentialSubmitter(mock_zep, min_interval=0.25).min_interval == 0.25

    def test_int_too_large_for_a_float_refused(self, mock_zep):
        # an int this big is not representable as a float, so it is as unusable
        # as +inf: accepting it only defers the failure to time.sleep, which
        # raises OverflowError once submit() paces its first episode. Refuse it
        # here, naming the parameter, rather than shipping that error downstream.
        with pytest.raises(ConfigurationError, match="min_interval"):
            SequentialSubmitter(mock_zep, min_interval=10**400)

    def test_finite_int_min_interval_accepted(self, mock_zep):
        # the guard is finiteness, not "is not an int"
        assert SequentialSubmitter(mock_zep, min_interval=3).min_interval == 3


class TestRateLimits:
    def test_retry_after_http_date_in_the_past_clamps_to_zero(self):
        error = ApiError(status_code=429, headers={"Retry-After": "Thu, 01 Jan 1970 00:00:00 GMT"})
        assert _retry_after_seconds(error) == 0.0

    def test_malformed_retry_after_falls_back_to_backoff(self):
        error = ApiError(status_code=429, headers={"Retry-After": "not-a-date"})
        assert _retry_after_seconds(error) is None

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
        assert result.add_errors[0].error == "graph.add failed: status=400, body=bad request"
        assert sleeps == []

    def test_server_error_is_not_retried_without_idempotency(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=503),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert len(result.add_errors) == 1
        assert mock_zep.graph.add.call_count == 1
        assert sleeps == []

    def test_absurd_retry_after_is_capped(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=429, headers={"Retry-After": "86400"}),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert result.episode_uuids == ["uuid-0"]
        assert sleeps == [MAX_RETRY_WAIT_SECONDS]

    def test_negative_retry_after_clamps_to_zero(self):
        error = ApiError(status_code=429, headers={"Retry-After": "-1"})
        assert _retry_after_seconds(error) == 0.0

    @pytest.mark.parametrize("header", ["nan", "inf", "-inf"])
    def test_non_finite_retry_after_falls_back_to_backoff(self, header):
        error = ApiError(status_code=429, headers={"Retry-After": header})
        assert _retry_after_seconds(error) is None

    @pytest.mark.parametrize(
        ("header", "expected_sleep"),
        [("-1", 0.0), ("2", 2.0)],
    )
    def test_out_of_range_retry_after_still_retries(self, mock_zep, sleeps, header, expected_sleep):
        mock_zep.graph.add.side_effect = [
            ApiError(status_code=429, headers={"Retry-After": header}),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert result.add_errors == []
        assert result.episode_uuids == ["uuid-0"]
        assert sleeps == [expected_sleep]

    @pytest.mark.parametrize("header", ["-1", "nan", "2"])
    def test_malformed_retry_after_returns_error_rather_than_raising(self, sleeps, header):
        """call_with_retries promises (None, last_error); a header that reaches
        time.sleep as a negative or NaN value must not turn into a ValueError."""

        def always_rate_limited():
            raise ApiError(status_code=429, headers={"Retry-After": header})

        result, error = call_with_retries(always_rate_limited, max_retries=2)
        assert result is None
        assert isinstance(error, ApiError)
        assert error.status_code == 429


class TestTransportErrors:
    """The SDK raises httpx errors untouched and never retries them itself."""

    def test_read_timeout_records_error_and_returns_written_uuids(self, mock_zep, sleeps):
        def side_effect(**kwargs):
            if kwargs["data"] == "episode 2":
                raise httpx.ReadTimeout("response never arrived")
            return make_zep_episode(kwargs["data"])

        mock_zep.graph.add.side_effect = side_effect
        result = SequentialSubmitter(mock_zep).submit(episodes(4), DEST)
        # the caller still gets a result carrying every episode already written
        assert result.episode_uuids == ["episode 0", "episode 1", "episode 3"]
        assert result.items_submitted == 3
        [error] = result.add_errors
        assert error.index == 2
        assert error.error == "graph.add failed: transport error ReadTimeout"
        assert result.status == "partial"

    def test_read_timeout_is_not_retried_without_idempotency(self, mock_zep, sleeps):
        # the request went out, so the write may already have landed
        mock_zep.graph.add.side_effect = [
            httpx.ReadTimeout("response never arrived"),
            make_zep_episode("uuid-0"),
        ]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert mock_zep.graph.add.call_count == 1
        assert len(result.add_errors) == 1
        assert sleeps == []

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("connection refused"),
            httpx.ConnectTimeout("connect timed out"),
            httpx.PoolTimeout("no connection available"),
        ],
    )
    def test_unsent_transport_error_is_retried(self, mock_zep, sleeps, error):
        # the request never reached the server, so retrying cannot duplicate it
        mock_zep.graph.add.side_effect = [error, make_zep_episode("uuid-0")]
        result = SequentialSubmitter(mock_zep).submit(episodes(1), DEST)
        assert mock_zep.graph.add.call_count == 2
        assert result.add_errors == []
        assert result.episode_uuids == ["uuid-0"]
        assert len(sleeps) == 1

    def test_exhausted_transport_retries_record_error_and_continue(self, mock_zep, sleeps):
        def side_effect(**kwargs):
            if kwargs["data"] == "episode 0":
                raise httpx.ConnectError("connection refused")
            return make_zep_episode(kwargs["data"])

        mock_zep.graph.add.side_effect = side_effect
        result = SequentialSubmitter(mock_zep, max_retries=2).submit(episodes(2), DEST)
        assert len(result.add_errors) == 1
        assert result.episode_uuids == ["episode 1"]

    def test_transport_error_does_not_contain_episode_data(self, mock_zep, sleeps):
        mock_zep.graph.add.side_effect = httpx.ReadTimeout("SENSITIVE-CONTENT")
        result = SequentialSubmitter(mock_zep).submit([Episode(data="SENSITIVE-CONTENT")], DEST)
        assert "SENSITIVE-CONTENT" not in result.add_errors[0].error
