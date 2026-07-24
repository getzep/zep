"""Tests for search_when_ready — the indexing-lag-aware search helper."""

from types import SimpleNamespace

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.verify import search_when_ready


def results(n_edges: int):
    class _Results:
        edges = [object()] * n_edges
        nodes = None
        episodes = None

    return _Results()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.verify.time.sleep", lambda _: None)


class TestSearchWhenReady:
    def test_returns_first_non_empty_response(self, mock_zep):
        mock_zep.graph.search.side_effect = [results(0), results(0), results(3)]
        response = search_when_ready(mock_zep, "who works here?", graph_id="g1")
        assert len(response.edges) == 3
        assert mock_zep.graph.search.call_count == 3

    def test_no_polling_when_results_immediate(self, mock_zep):
        mock_zep.graph.search.return_value = results(2)
        search_when_ready(mock_zep, "q", graph_id="g1")
        assert mock_zep.graph.search.call_count == 1

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("context", "assembled context"),
            ("observations", [object()]),
            ("thread_summaries", [object()]),
        ],
    )
    def test_all_supported_result_fields_stop_polling(self, mock_zep, field, value):
        response = SimpleNamespace(
            context=None,
            edges=None,
            nodes=None,
            episodes=None,
            observations=None,
            thread_summaries=None,
        )
        setattr(response, field, value)
        mock_zep.graph.search.return_value = response

        assert search_when_ready(mock_zep, "q", graph_id="g1") is response
        assert mock_zep.graph.search.call_count == 1

    def test_returns_empty_response_after_timeout(self, mock_zep, monkeypatch):
        clock = iter(range(0, 10_000, 60))  # each check jumps 60s
        monkeypatch.setattr("zep_ingest.verify.time.monotonic", lambda: next(clock))
        mock_zep.graph.search.return_value = results(0)
        response = search_when_ready(mock_zep, "q", graph_id="g1", timeout=120)
        assert response.edges == []
        assert mock_zep.graph.search.call_count >= 2

    def test_user_graph_destination(self, mock_zep):
        mock_zep.graph.search.return_value = results(1)
        search_when_ready(mock_zep, "q", user_id="u1")
        assert mock_zep.graph.search.call_args.kwargs["user_id"] == "u1"

    def test_destination_required(self, mock_zep):
        with pytest.raises(ConfigurationError):
            search_when_ready(mock_zep, "q")
        mock_zep.graph.search.assert_not_called()
