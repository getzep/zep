"""search_when_ready: graph search that absorbs ingestion's indexing lag.

Ingestion is asynchronous end to end: even after ``IngestResult.wait()``
reports success, just-written facts take a few more seconds to become
searchable. Every "ingest then immediately search" script hits this window;
this helper owns the retry so callers don't hand-roll poll loops.
"""

import time
from typing import TYPE_CHECKING, Any, Literal

from zep_ingest._validation import require_int_range, require_nonnegative_number
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Destination

if TYPE_CHECKING:
    from zep_cloud.client import Zep

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_SECONDS = 5.0

#: v4 replaces the single ``graph.search`` with one method per scope.
Scope = Literal["edges", "nodes", "episodes", "observations", "thread_summaries"]
_SEARCH_METHODS: dict[str, str] = {
    "edges": "search_edges",
    "nodes": "search_nodes",
    "episodes": "search_episodes",
    "observations": "search_observations",
    "thread_summaries": "search_thread_summaries",
}


def search_when_ready(
    client: "Zep",
    query: str,
    *,
    graph_uuid: str | None = None,
    scope: Scope = "edges",
    limit: int = 10,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_SECONDS,
    **search_kwargs: Any,
) -> list[Any]:
    """Run the v4 search of ``scope``, retrying an empty result until indexing
    catches up.

    v4 has one search method for each scope, and each one returns a pager. The
    first page of results is returned, so the caller gets the same "did my data
    arrive" answer as the v3 helper gave. The result is an empty list once
    ``timeout`` seconds have elapsed — an empty result is a valid answer, so
    this helper does not raise.
    """
    require_int_range("limit", limit, minimum=1)
    require_nonnegative_number("timeout", timeout)
    require_nonnegative_number("poll_interval", poll_interval)
    if scope not in _SEARCH_METHODS:
        raise ConfigurationError(f"scope must be one of {sorted(_SEARCH_METHODS)}, got {scope!r}")
    destination = Destination(graph_uuid=graph_uuid)
    search = getattr(client.graph, _SEARCH_METHODS[scope])
    deadline = time.monotonic() + timeout
    while True:
        pager = search(destination.graph, query=query, limit=limit, **search_kwargs)
        items = list(pager.items or [])
        if items:
            return items
        if time.monotonic() >= deadline:
            return items
        time.sleep(poll_interval)
