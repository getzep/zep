"""Tests for V4 graph storage."""

from unittest.mock import MagicMock, patch

import pytest
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

from zep_crewai import ZepGraphStorage


def _client() -> MagicMock:
    client = MagicMock(spec=Zep)
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    return client


def test_initialization_uses_graph_uuid() -> None:
    storage = ZepGraphStorage(_client(), graph_uuid="graph-uuid")
    assert storage.graph_uuid == "graph-uuid"


def test_initialization_requires_graph_uuid() -> None:
    with pytest.raises(ValueError, match="graph_uuid is required"):
        ZepGraphStorage(_client(), graph_uuid="")


def test_initialization_requires_zep_client() -> None:
    with pytest.raises(TypeError, match="client must be an instance of Zep"):
        ZepGraphStorage("invalid", graph_uuid="graph-uuid")  # type: ignore[arg-type]


def test_graph_storage_does_not_accept_user_creation_hook() -> None:
    with pytest.raises(TypeError):
        ZepGraphStorage(
            _client(),
            graph_uuid="g",
            on_created=lambda client, user: None,  # type: ignore[call-arg]
        )


def test_save_text_uses_episode_add() -> None:
    client = _client()
    ZepGraphStorage(client, "g").save("fact", {"type": "text"})
    client.graph.episode.add.assert_called_once_with(
        "g", data="fact", type="text", metadata={"type": "text"}
    )


def test_save_json_uses_episode_add() -> None:
    client = _client()
    ZepGraphStorage(client, "g").save('{"a": 1}', {"type": "json"})
    assert client.graph.episode.add.call_args.kwargs["type"] == "json"


def test_save_defaults_to_text() -> None:
    client = _client()
    ZepGraphStorage(client, "g").save("fact")
    assert client.graph.episode.add.call_args.kwargs["type"] == "text"


def test_save_does_not_raise_on_sdk_error(caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    client.graph.episode.add.side_effect = RuntimeError("offline")
    with caplog.at_level("ERROR"):
        ZepGraphStorage(client, "g").save("fact")
    assert "offline" in caplog.text


@patch("zep_crewai.graph_storage.search_graph_and_compose_context")
def test_search_returns_context_result(search: MagicMock) -> None:
    client = _client()
    search.return_value = "<ZEP_CONTEXT>fact</ZEP_CONTEXT>"
    results = ZepGraphStorage(client, "g").search("query", limit=5)
    assert results == [
        {
            "context": "<ZEP_CONTEXT>fact</ZEP_CONTEXT>",
            "type": "graph_context",
            "source": "graph",
            "query": "query",
        }
    ]
    assert search.call_args.args == (client, "query", "g")


@patch("zep_crewai.graph_storage.search_graph_and_compose_context", return_value=None)
def test_search_without_results_returns_empty(search: MagicMock) -> None:
    assert ZepGraphStorage(_client(), "g").search("query") == []


@patch("zep_crewai.graph_storage.search_graph_and_compose_context")
def test_search_passes_context_configuration(search: MagicMock) -> None:
    filters = SearchFilters(node_labels=["Project"])
    storage = ZepGraphStorage(
        _client(),
        "g",
        search_filters=filters,
        context_template="{context}",
        template_uuid="template-uuid",
        max_characters=2000,
    )
    storage.search("query")
    assert search.call_args.kwargs == {
        "search_filters": filters,
        "context_template": "{context}",
        "template_uuid": "template-uuid",
        "max_characters": 2000,
    }


def test_reset_does_not_delete_graph() -> None:
    client = _client()
    ZepGraphStorage(client, "g").reset()
    client.graph.delete.assert_not_called()
