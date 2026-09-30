"""Resolution of the graph UUID that a store or a search tool uses.

Zep v4 addresses a graph by UUID. A standalone graph gives its UUID directly.
A user graph does not: the application holds the UUID of the user, and the
UUID of the user graph comes from the user record.

:class:`GraphUuidResolver` does that one read (``user.get``) the first time a
store or a tool needs the graph, and then caches the result for the life of
the object. This is not a name lookup; the input is already a UUID.
"""

from __future__ import annotations

import asyncio

from zep_cloud.client import AsyncZep


class GraphUuidResolver:
    """Give the UUID of the graph to search and to write to.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user in user-graph mode.
        graph_uuid: The UUID of a standalone graph. When it is set, no read
            is necessary.
    """

    def __init__(
        self,
        client: AsyncZep,
        *,
        user_uuid: str | None = None,
        graph_uuid: str | None = None,
    ) -> None:
        self._client = client
        self._user_uuid = user_uuid
        self._graph_uuid = graph_uuid
        self._lock = asyncio.Lock()

    async def resolve(self) -> str:
        """Return the graph UUID, reading the user record one time if necessary.

        Raises:
            ValueError: If neither a graph UUID nor a user UUID is available,
                or if the user has no graph.
        """
        if self._graph_uuid is not None:
            return self._graph_uuid
        if self._user_uuid is None:
            raise ValueError("A user_uuid or a graph_uuid is required to reach a Zep graph.")

        async with self._lock:
            if self._graph_uuid is None:
                user = await self._client.user.get(self._user_uuid)
                if user.graph_uuid is None:
                    raise ValueError(
                        f"Zep user {self._user_uuid} has no graph. "
                        "Confirm that the user UUID is correct."
                    )
                self._graph_uuid = user.graph_uuid
        return self._graph_uuid
