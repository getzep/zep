"""Live integration tests (require ZEP_API_KEY; run automatically in CI).

Kept minimal: one sequential round-trip and one batch round-trip against a
throwaway graph that is deleted afterwards. The batch test skips gracefully
on plans without Batch API access.
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


def test_batch_still_drops_thread_message_created_at(zep):
    """Canary for the auto->sequential workaround in threads.py.

    The Batch API currently ignores created_at on thread_message items, so
    ingest_thread_messages(method="auto") routes timestamped backfills through
    the sequential path. When THIS TEST FAILS, Zep has fixed the bug: remove
    the workaround (and this canary).
    """
    from zep_ingest import ThreadMessage, ingest_thread_messages

    user_id = f"zep-ingest-it-{uuid.uuid4().hex[:12]}"
    zep.user.add(user_id=user_id)
    try:
        message = ThreadMessage(
            thread_id=f"canary-{user_id}",
            role="user",
            content="Canary: does the Batch API preserve created_at yet?",
            created_at="2024-06-15T10:30:00Z",
        )
        try:
            result = ingest_thread_messages(zep, [message], user_id=user_id, method="batch")
        except BatchUnavailableError:
            pytest.skip("Batch API not enabled for this ZEP_API_KEY")
        result.wait(poll_interval=5.0, timeout=600)
        episodes = zep.graph.episode.get_by_user_id(user_id, lastn=5)
        [episode] = episodes.episodes or []
        assert not str(episode.created_at).startswith("2024-06-15"), (
            "The Batch API now preserves created_at on thread_message items — "
            "remove the auto->sequential workaround in threads.py and this canary."
        )
    finally:
        zep.user.delete(user_id)
