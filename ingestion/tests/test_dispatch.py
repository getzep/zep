"""Tests for submit_episodes dispatch: method="auto" | "batch" | "sequential"."""

import httpx
import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.types.batch_summary import BatchSummary

from tests.conftest import make_batch_summary, make_zep_episode
from zep_ingest.exceptions import BatchUnavailableError, InvalidBatchResponseError
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

    def test_falls_back_to_sequential_when_endpoint_not_found(self, mock_zep, caplog):
        # A 404 is the one failure sequential ingestion is unaffected by: the
        # deployment simply does not serve the batch endpoint.
        mock_zep.batch.create.side_effect = ApiError(status_code=404, body="not found")
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
        mock_zep.batch.create.side_effect = ApiError(status_code=404)
        mock_zep.graph.add.side_effect = [make_zep_episode(f"u{i}") for i in range(5)]
        result = submit_episodes(mock_zep, iter(episodes(5)), DEST, method="auto")
        datas = [c.kwargs["data"] for c in mock_zep.graph.add.call_args_list]
        assert datas == [f"episode {i}" for i in range(5)]
        assert result.items_submitted == 5

    @pytest.mark.parametrize("status_code", [402, 403])
    def test_refused_probe_propagates_without_fallback(self, mock_zep, status_code):
        # A refused key or an exhausted quota would refuse graph.add too, so
        # grinding through the stream sequentially would only bury the real
        # error in per-item failures. It must surface as itself.
        mock_zep.batch.create.side_effect = ApiError(status_code=status_code, body="refused")
        with pytest.raises(ApiError) as caught:
            submit_episodes(mock_zep, episodes(3), DEST, method="auto")
        assert caught.value.status_code == status_code
        # the server's own explanation reaches the caller intact
        assert caught.value.body == "refused"
        mock_zep.graph.add.assert_not_called()

    def test_server_error_on_create_propagates(self, mock_zep):
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
        # downgrade to sequential, which would only mask a server fault the
        # sequential path would hit too.
        mock_zep.batch.create.side_effect = ApiError(status_code=500)
        with pytest.raises(ApiError):
            submit_episodes(mock_zep, episodes(3), DEST, method="auto", max_add_retries=3)
        assert mock_zep.batch.create.call_count == 1
        mock_zep.graph.add.assert_not_called()

    def test_probe_transport_error_surfaces_without_fallback(self, mock_zep):
        # A transport error carries no status, so it can never be mistaken for
        # a missing endpoint and downgraded to sequential.
        mock_zep.batch.create.side_effect = httpx.ReadTimeout("response never arrived")
        with pytest.raises(httpx.ReadTimeout):
            submit_episodes(mock_zep, episodes(3), DEST, method="auto", max_add_retries=3)
        assert mock_zep.batch.create.call_count == 1
        mock_zep.graph.add.assert_not_called()

    def test_empty_stream_no_probe(self, mock_zep):
        result = submit_episodes(mock_zep, [], DEST, method="auto")
        mock_zep.batch.create.assert_not_called()
        assert result.items_submitted == 0

    @pytest.mark.parametrize("batch_id", [None, "", "   "])
    def test_probe_without_usable_batch_id_does_not_open_second_batch(self, mock_zep, batch_id):
        mock_zep.batch.create.return_value = BatchSummary(batch_id=batch_id, status="draft")

        with pytest.raises(InvalidBatchResponseError, match="batch_id"):
            submit_episodes(mock_zep, episodes(1), DEST, method="auto")

        assert mock_zep.batch.create.call_count == 1
        mock_zep.batch.add.assert_not_called()


class TestExplicit:
    def test_batch_raises_batch_unavailable_when_endpoint_not_found(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=404)
        with pytest.raises(BatchUnavailableError):
            submit_episodes(mock_zep, episodes(1), DEST, method="batch")

    @pytest.mark.parametrize("status_code", [402, 403])
    def test_batch_surfaces_refusal_instead_of_batch_unavailable(self, mock_zep, status_code):
        # method="batch" was asked for explicitly, so a refusal is reported as
        # the refusal it is, not as "this deployment has no Batch API".
        mock_zep.batch.create.side_effect = ApiError(status_code=status_code, body="refused")
        with pytest.raises(ApiError) as caught:
            submit_episodes(mock_zep, episodes(1), DEST, method="batch")
        assert not isinstance(caught.value, BatchUnavailableError)
        assert caught.value.status_code == status_code

    def test_sequential_never_touches_batch(self, mock_zep):
        submit_episodes(mock_zep, episodes(2), DEST, method="sequential")
        mock_zep.batch.create.assert_not_called()
        mock_zep.batch.add.assert_not_called()
