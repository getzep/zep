"""search_when_ready: graph search that absorbs ingestion's indexing lag.

Ingestion is asynchronous end to end: even after ``IngestResult.wait()``
reports success, just-written facts take a few more seconds to become
searchable. Every "ingest then immediately search" script hits this window;
this helper owns the retry so callers don't hand-roll poll loops.
"""

import time
from typing import TYPE_CHECKING, Any

from zep_ingest.types import Destination

if TYPE_CHECKING:
    from zep_cloud.client import Zep
    from zep_cloud.types.graph_search_results import GraphSearchResults

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_SECONDS = 5.0


def search_when_ready(
    client: "Zep",
    query: str,
    *,
    graph_id: str | None = None,
    user_id: str | None = None,
    scope: str = "edges",
    limit: int = 10,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_SECONDS,
    **search_kwargs: Any,
) -> "GraphSearchResults":
    """Run ``graph.search``, retrying an empty result until indexing catches up.

    Returns the first response with any hits, or the final (empty) response
    once ``timeout`` seconds have elapsed — it never raises on empty results,
    since "nothing matched" is a valid answer.
    """
    destination = Destination(graph_id=graph_id, user_id=user_id)
    target = (
        {"graph_id": destination.graph_id}
        if destination.graph_id is not None
        else {"user_id": destination.user_id}
    )
    deadline = time.monotonic() + timeout
    while True:
        response = client.graph.search(
            query=query, scope=scope, limit=limit, **target, **search_kwargs
        )
        if response.edges or response.nodes or response.episodes:
            return response
        if time.monotonic() >= deadline:
            return response
        time.sleep(poll_interval)
