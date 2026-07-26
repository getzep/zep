"""Live integration tests (require ZEP_API_KEY; run automatically in CI).

Kept minimal: one sequential round-trip and one batch round-trip against a
throwaway graph that is deleted afterwards. The batch test skips gracefully
on deployments that do not serve the batch endpoint.
"""

import os
import uuid

import pytest

from zep_ingest import Episode, ingest
from zep_ingest.exceptions import BatchUnavailableError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("ZEP_API_KEY"), reason="ZEP_API_KEY not set"),
]


class ListLoader:
    def __init__(self, episodes):
        self.episodes = episodes

    def load(self):
        yield from self.episodes


@pytest.fixture
def zep():
    from zep_cloud.client import Zep

    return Zep(api_key=os.environ["ZEP_API_KEY"])


@pytest.fixture
def graph_id(zep):
    graph_id = f"zep-ingest-it-{uuid.uuid4().hex[:12]}"
    zep.graph.create(graph_id=graph_id, name="zep-ingest integration test")
    yield graph_id
    zep.graph.delete(graph_id)


EPISODES = [
    Episode(
        data="Avery Brown joined the engineering team as a senior developer.",
        created_at="2024-06-15T10:30:00Z",
    ),
    Episode(
        data="Avery Brown was promoted to tech lead of the engineering team.",
        created_at="2024-09-01T09:00:00Z",
    ),
]


def test_sequential_round_trip(zep, graph_id):
    result = ingest(zep, ListLoader(EPISODES), graph_id=graph_id, method="sequential")
    assert result.items_submitted == 2
    assert result.add_errors == []
    assert len(result.episode_uuids) == 2
    result.wait(poll_interval=5.0, timeout=300)
    assert result.status == "succeeded"


def test_batch_round_trip(zep, graph_id):
    try:
        result = ingest(zep, ListLoader(EPISODES), graph_id=graph_id, method="batch")
    except BatchUnavailableError:
        pytest.skip("Batch API not enabled for this ZEP_API_KEY")
    assert result.items_submitted == 2
    assert result.batch_ids
    result.wait(poll_interval=5.0, timeout=600)
    assert result.status in ("succeeded", "partial")
