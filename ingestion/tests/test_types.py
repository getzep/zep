"""Tests for core data model: Episode, Destination, and API mappings."""

import pytest
from zep_cloud.types.batch_add_item import BatchAddItem

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import (
    MAX_EPISODE_CHARS,
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    MAX_METADATA_KEYS,
    SAFE_EPISODE_CHARS,
    Destination,
    Episode,
    to_batch_item,
    to_graph_add_kwargs,
)


class TestConstants:
    def test_limits_match_documented_api_constraints(self):
        assert MAX_EPISODE_CHARS == 10_000
        assert SAFE_EPISODE_CHARS == 9_500
        assert MAX_ITEMS_PER_ADD == 350
        assert MAX_ITEMS_PER_BATCH == 50_000
        assert MAX_METADATA_KEYS == 10


class TestEpisode:
    def test_defaults(self):
        ep = Episode(data="hello")
        assert ep.data == "hello"
        assert ep.data_type == "text"
        assert ep.created_at is None
        assert ep.metadata is None
        assert ep.source_description is None
        assert ep.document is None

    def test_document_excluded_from_repr(self):
        ep = Episode(data="chunk", document="a" * 100)
        assert "document" not in repr(ep)


class TestDestination:
    def test_graph_id_only_is_valid(self):
        dest = Destination(graph_id="g1")
        assert dest.graph_id == "g1"
        assert dest.user_id is None

    def test_user_id_only_is_valid(self):
        dest = Destination(user_id="u1")
        assert dest.user_id == "u1"

    def test_both_raises(self):
        with pytest.raises(ConfigurationError):
            Destination(graph_id="g1", user_id="u1")

    def test_neither_raises(self):
        with pytest.raises(ConfigurationError):
            Destination()

    def test_frozen(self):
        dest = Destination(graph_id="g1")
        with pytest.raises(AttributeError):
            dest.graph_id = "other"  # type: ignore[misc]


class TestToBatchItem:
    def test_maps_all_fields(self):
        ep = Episode(
            data="hello",
            data_type="message",
            created_at="2024-06-15T10:30:00Z",
            metadata={"source": "slack"},
            source_description="Slack #general export",
        )
        item = to_batch_item(ep, Destination(graph_id="g1"))
        assert isinstance(item, BatchAddItem)
        assert item.type == "graph_episode"
        assert item.data == "hello"
        assert item.data_type == "message"
        assert item.created_at == "2024-06-15T10:30:00Z"
        assert item.metadata == {"source": "slack"}
        assert item.source_description == "Slack #general export"
        assert item.graph_id == "g1"
        assert item.user_id is None

    def test_user_destination(self):
        item = to_batch_item(Episode(data="x"), Destination(user_id="u1"))
        assert item.user_id == "u1"
        assert item.graph_id is None

    def test_metadata_over_limit_rejected(self):
        metadata = {f"k{i}": i for i in range(12)}
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata=metadata)


class TestToGraphAddKwargs:
    def test_maps_all_fields(self):
        ep = Episode(
            data="hello",
            data_type="text",
            created_at="2024-06-15T10:30:00Z",
            metadata={"source": "docs"},
            source_description="handbook",
        )
        kwargs = to_graph_add_kwargs(ep, Destination(user_id="u1"))
        assert kwargs == {
            "data": "hello",
            "type": "text",
            "created_at": "2024-06-15T10:30:00Z",
            "metadata": {"source": "docs"},
            "source_description": "handbook",
            "user_id": "u1",
        }

    def test_omits_unset_optional_fields(self):
        kwargs = to_graph_add_kwargs(Episode(data="x"), Destination(graph_id="g1"))
        assert kwargs == {"data": "x", "type": "text", "graph_id": "g1"}

    def test_metadata_over_limit_rejected(self):
        metadata = {f"k{i}": i for i in range(11)}
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata=metadata)
