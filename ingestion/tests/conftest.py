"""Shared fixtures: mock Zep client and builders for realistic API return values."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep
from zep_cloud.types.batch_item_detail import BatchItemDetail
from zep_cloud.types.batch_item_list_response import BatchItemListResponse
from zep_cloud.types.batch_progress import BatchProgress
from zep_cloud.types.batch_summary import BatchSummary
from zep_cloud.types.episode import Episode as ZepEpisode


def make_batch_summary(
    batch_id: str = "batch-1", status: str = "queued", **progress: int
) -> BatchSummary:
    return BatchSummary(
        batch_id=batch_id,
        status=status,  # type: ignore[arg-type]
        progress=BatchProgress(**progress) if progress else None,
    )


def make_zep_episode(uuid: str = "ep-1", processed: bool = False) -> ZepEpisode:
    return ZepEpisode(
        uuid_=uuid, processed=processed, content="", created_at="2024-01-01T00:00:00Z"
    )


def make_item_detail(status: str = "failed", **kwargs: Any) -> BatchItemDetail:
    return BatchItemDetail(status=status, **kwargs)  # type: ignore[arg-type]


def make_item_list(
    items: list[BatchItemDetail], next_cursor: int | None = None
) -> BatchItemListResponse:
    return BatchItemListResponse(items=items, next_cursor=next_cursor)


@pytest.fixture
def mock_zep() -> MagicMock:
    """A MagicMock speccing the sync Zep client with the surfaces zep-ingest uses."""
    client = MagicMock(spec=Zep)
    client.batch = MagicMock()
    client.batch.create = MagicMock(return_value=make_batch_summary("batch-1", "draft"))
    client.batch.add = MagicMock()
    client.batch.process = MagicMock(return_value=make_batch_summary("batch-1", "queued"))
    client.batch.get = MagicMock(return_value=make_batch_summary("batch-1", "succeeded"))
    client.batch.list_items = MagicMock(return_value=make_item_list([]))
    client.graph = MagicMock()
    client.graph.add = MagicMock(return_value=make_zep_episode("ep-1", processed=False))
    client.graph.episode = MagicMock()
    client.graph.episode.get = MagicMock(return_value=make_zep_episode("ep-1", processed=True))
    client.graph.create = MagicMock()
    client.graph.get = MagicMock()
    client.graph.set_ontology = MagicMock()
    client.graph.add_fact_triple = MagicMock()
    client.graph.node = MagicMock()
    client.graph.edge = MagicMock()
    client.user = MagicMock()
    client.user.add = MagicMock()
    client.user.get = MagicMock()
    client.thread = MagicMock()
    client.thread.create = MagicMock()
    client.thread.add_messages = MagicMock()
    return client
