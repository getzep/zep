"""Tests for submit_episodes dispatch: method="auto" | "batch" | "sequential"."""

import pytest
from zep_cloud.core.api_error import ApiError

from tests.conftest import make_batch_summary, make_zep_episode
from zep_ingest.exceptions import BatchUnavailableError
from zep_ingest.submitters import submit_episodes
from zep_ingest.types import Destination, Episode

DEST = Destination(graph_id="g1")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda _: None)


def episodes(n: int) -> list[Episode]:
    return [Episode(data=f"episode {i}") for i in range(n)]


class TestAuto:
    def test_uses_batch_when_available(self, mock_zep):
        result = submit_episodes(mock_zep, episodes(3), DEST, method="auto")
        assert result.method == "batch"
        mock_zep.batch.add.assert_called_once()
        mock_zep.graph.add.assert_not_called()

    def test_falls_back_to_sequential_on_gating_error(self, mock_zep, caplog):
        mock_zep.batch.create.side_effect = ApiError(status_code=403, body="forbidden")
        mock_zep.graph.add.side_effect = [make_zep_episode(f"u{i}") for i in range(3)]
        with caplog.at_level("INFO"):
            result = submit_episodes(mock_zep, episodes(3), DEST, method="auto")
        assert result.method == "sequential"
        assert result.items_submitted == 3
        assert mock_zep.graph.add.call_count == 3
        assert any("Batch API" in message for message in caplog.messages)
        # the notice must also reach consumers who don't configure logging
        assert any("Batch API" in warning for warning in result.warnings)

    def test_no_episodes_lost_on_fallback(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=402)
        mock_zep.graph.add.side_effect = [make_zep_episode(f"u{i}") for i in range(5)]
        result = submit_episodes(mock_zep, iter(episodes(5)), DEST, method="auto")
        datas = [c.kwargs["data"] for c in mock_zep.graph.add.call_args_list]
        assert datas == [f"episode {i}" for i in range(5)]
        assert result.items_submitted == 5

    def test_non_gating_create_error_propagates(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=500)
        with pytest.raises(ApiError):
            submit_episodes(mock_zep, episodes(1), DEST, method="auto")

    def test_probe_does_not_retry_ambiguous_server_error(self, mock_zep):
        # batch.create is non-idempotent: a 503 may have created a batch before
        # the response was lost, so it must surface instead of being retried.
        mock_zep.batch.create.side_effect = [
            ApiError(status_code=503, body="unavailable"),
            make_batch_summary("batch-1", "draft"),
        ]
        with pytest.raises(ApiError):
            submit_episodes(mock_zep, episodes(3), DEST, method="auto")
        assert mock_zep.batch.create.call_count == 1

    def test_persistent_transient_probe_error_raises_without_fallback(self, mock_zep):
        # A 5xx that survives retries surfaces as an error — never a silent
        # downgrade to sequential (which would hit the same entitlements anyway).
        mock_zep.batch.create.side_effect = ApiError(status_code=500)
        with pytest.raises(ApiError):
            submit_episodes(mock_zep, episodes(3), DEST, method="auto", max_add_retries=3)
        assert mock_zep.batch.create.call_count == 1
        mock_zep.graph.add.assert_not_called()

    def test_empty_stream_no_probe(self, mock_zep):
        result = submit_episodes(mock_zep, [], DEST, method="auto")
        mock_zep.batch.create.assert_not_called()
        assert result.items_submitted == 0


class TestExplicit:
    def test_batch_raises_batch_unavailable_on_gating(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=403)
        with pytest.raises(BatchUnavailableError):
            submit_episodes(mock_zep, episodes(1), DEST, method="batch")

    def test_sequential_never_touches_batch(self, mock_zep):
        submit_episodes(mock_zep, episodes(2), DEST, method="sequential")
        mock_zep.batch.create.assert_not_called()
        mock_zep.batch.add.assert_not_called()
