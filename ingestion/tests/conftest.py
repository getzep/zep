"""Shared fixtures: mock Zep client and builders for realistic API return values."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from zep_cloud.client import Zep
from zep_cloud.types.add_edge_result import AddEdgeResult
from zep_cloud.types.add_episode_result import AddEpisodeResult
from zep_cloud.types.add_messages_result import AddMessagesResult
from zep_cloud.types.add_nodes_result import AddNodesResult
from zep_cloud.types.added_edge import AddedEdge
from zep_cloud.types.added_node import AddedNode
from zep_cloud.types.batch import Batch
from zep_cloud.types.batch_item import BatchItem
from zep_cloud.types.episode import Episode as ZepEpisode
from zep_cloud.types.process_batch_result import ProcessBatchResult
from zep_cloud.types.task import Task

TASK_UUID = "22222222-2222-4222-8222-222222222222"
BATCH_UUID = "33333333-3333-4333-8333-333333333333"
THREAD_UUID = "44444444-4444-4444-8444-444444444444"
GRAPH_UUID = "55555555-5555-4555-8555-555555555555"


def make_task(uuid: str = TASK_UUID, status: str = "succeeded", **kwargs: Any) -> Task:
    return Task(uuid_=uuid, status=status, **kwargs)


def make_batch_summary(
    batch_id: str = BATCH_UUID, status: str = "queued", **progress: int
) -> Batch:
    return Batch(
        uuid_=batch_id,
        status=status,
        progress=dict(progress) if progress else None,
    )


def make_zep_episode(uuid: str = "ep-1", processed: bool = False) -> ZepEpisode:
    return ZepEpisode(
        uuid_=uuid, processed=processed, content="", created_at="2024-01-01T00:00:00Z"
    )


def make_episode_result(uuid: str = "ep-1", processed: bool = False) -> AddEpisodeResult:
    """A v4 ``graph.episode.add`` response, which nests the created episode."""
    return AddEpisodeResult(episode=make_zep_episode(uuid, processed), task=make_task())


def make_item_detail(status: str = "failed", **kwargs: Any) -> BatchItem:
    return BatchItem(status=status, **kwargs)  # type: ignore[arg-type]


def make_item_list(items: list[BatchItem]) -> list[BatchItem]:
    """v4 ``batch.list_items`` returns a pager, which the caller iterates."""
    return list(items)


@pytest.fixture
def mock_zep() -> MagicMock:
    """A MagicMock speccing the sync Zep client with the surfaces zep-ingest uses."""
    client = MagicMock(spec=Zep)
    client.batch = MagicMock()
    client.batch.create = MagicMock(return_value=make_batch_summary(BATCH_UUID, "draft"))
    client.batch.add_items = MagicMock()
    client.batch.process = MagicMock(
        return_value=ProcessBatchResult(batch=make_batch_summary(BATCH_UUID, "queued"))
    )
    client.batch.get = MagicMock(return_value=make_batch_summary(BATCH_UUID, "succeeded"))
    client.batch.list_items = MagicMock(return_value=make_item_list([]))
    client.graph = MagicMock()
    client.graph.episode = MagicMock()
    client.graph.episode.add = MagicMock(
        return_value=AddEpisodeResult(
            episode=make_zep_episode("ep-1", processed=False), task=make_task()
        )
    )
    client.graph.episode.get = MagicMock(return_value=make_zep_episode("ep-1", processed=True))
    client.graph.create = MagicMock()
    client.graph.get = MagicMock()
    client.graph.set_ontology = MagicMock()
    client.graph.node = MagicMock()
    client.graph.node.add = MagicMock(
        return_value=AddNodesResult(
            task=make_task(),
            nodes=[AddedNode(name="node", uuid_="11111111-1111-4111-8111-111111111111")],
        )
    )
    client.graph.edge = MagicMock()
    client.graph.edge.add = MagicMock(
        return_value=AddEdgeResult(
            task=make_task(),
            edge=AddedEdge(uuid_="66666666-6666-4666-8666-666666666666"),
        )
    )
    client.user = MagicMock()
    client.user.create = MagicMock()
    client.thread = MagicMock()
    client.thread.create = MagicMock()
    client.thread.add_messages = MagicMock(return_value=AddMessagesResult(task=make_task()))
    client.task = MagicMock()
    client.task.get = MagicMock(return_value=make_task())
    return client
