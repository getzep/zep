"""
Zep Graph Memory Manager for AG2.

This module provides the ZepGraphMemoryManager class for integrating Zep's
knowledge graph capabilities with AG2 agents.

This is used for named/shared knowledge graphs, as opposed to the graph of a
user that ZepMemoryManager manages. Zep v4 addresses every graph by its
server-generated UUID.
"""

import logging
from typing import Any

from zep_cloud.client import AsyncZep, Zep

from zep_ag2.exceptions import ZepAG2ConfigError
from zep_ag2.tools import GRAPH_MAX_CHARS, _require_sync_client, _truncate

logger = logging.getLogger(__name__)


class ZepGraphMemoryManager:
    """
    Manages Zep knowledge graph for AG2 agents.

    Provides methods to search, add data, and inject graph context into
    AG2 agent system messages.

    Example:
        >>> from zep_cloud.client import AsyncZep
        >>> from zep_ag2 import ZepGraphMemoryManager
        >>> zep = AsyncZep(api_key="your-key")
        >>> graph = await zep.graph.create(name="company_kb")
        >>> manager = ZepGraphMemoryManager(zep, graph_uuid=graph.uuid_)
        >>> results = await manager.search("Python frameworks")
    """

    def __init__(
        self,
        client: AsyncZep,
        graph_uuid: str,
        *,
        sync_client: Zep | None = None,
    ) -> None:
        """
        Initialize ZepGraphMemoryManager.

        Args:
            client: An initialized AsyncZep instance.
            graph_uuid: The UUID of the knowledge graph in Zep (required).
                Read it from ``graph.uuid_`` one time and store it in your
                own database.
            sync_client: Optional synchronous ``Zep`` client. The
                synchronous methods (:meth:`search_sync` and
                :meth:`add_data_sync`) call this client directly. These
                methods raise ``TypeError`` when it is not set.

        Raises:
            ZepAG2ConfigError: If client is not an AsyncZep instance or graph_uuid is empty.
            TypeError: If sync_client is set and is not a synchronous Zep client.
        """
        if not isinstance(client, AsyncZep):
            raise ZepAG2ConfigError("client must be an instance of AsyncZep")
        if not graph_uuid:
            raise ZepAG2ConfigError("graph_uuid is required")
        if sync_client is not None:
            _require_sync_client(sync_client)

        self._client = client
        self._sync_client = sync_client
        self._graph_uuid = graph_uuid

    @property
    def client(self) -> AsyncZep:
        """The underlying AsyncZep client."""
        return self._client

    @property
    def sync_client(self) -> Zep | None:
        """The synchronous Zep client of the synchronous methods, if set."""
        return self._sync_client

    def _require_manager_sync_client(self) -> Zep:
        """Return the synchronous client, or raise ``TypeError`` if it is not set."""
        if self._sync_client is None:
            raise TypeError(
                "The synchronous methods of ZepGraphMemoryManager need a "
                "synchronous Zep client. Pass sync_client=zep_cloud.client.Zep(...) "
                "when you create the manager."
            )
        return self._sync_client

    @property
    def graph_uuid(self) -> str:
        """The UUID of the knowledge graph."""
        return self._graph_uuid

    async def search(
        self,
        query: str,
        limit: int = 5,
        scope: str | None = "edges",
    ) -> list[dict[str, Any]]:
        """
        Search the knowledge graph.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.
            scope: Search scope — 'edges' (facts), 'nodes' (entities), or 'episodes'.
                Zep v4 has one search method for each scope.

        Returns:
            A list of result dicts with 'content', 'type', and metadata fields.
        """
        selected_scope = scope or "edges"
        try:
            if selected_scope == "edges":
                pager = await self._client.graph.search_edges(
                    self._graph_uuid, query=query, page_size=limit
                )
            elif selected_scope == "nodes":
                pager = await self._client.graph.search_nodes(
                    self._graph_uuid, query=query, page_size=limit
                )
            elif selected_scope == "episodes":
                pager = await self._client.graph.search_episodes(
                    self._graph_uuid, query=query, page_size=limit
                )
            else:
                logger.error("Unknown search scope: %s", selected_scope)
                return []
            return _search_results(selected_scope, pager.items)
        except Exception as e:
            logger.error("Zep graph search failed: %s", type(e).__name__)
            return []

    async def add_data(
        self,
        data: str,
        data_type: str = "text",
    ) -> bool:
        """
        Add data to the knowledge graph.

        Args:
            data: The text data to add.
            data_type: Type of data — 'text', 'json', or 'message'.

        Returns:
            True if data was added successfully, False otherwise.
        """
        try:
            await self._client.graph.episode.add(
                self._graph_uuid,
                type=data_type,
                data=_truncate(data, GRAPH_MAX_CHARS, "graph data"),
            )
            return True
        except Exception as e:
            logger.error("Zep graph.episode.add failed: %s", type(e).__name__)
            return False

    async def enrich_system_message(
        self,
        agent: Any,
        query: str | None = None,
        limit: int = 5,
    ) -> None:
        """
        Inject knowledge graph context into an AG2 agent's system message.

        If a query is provided, searches the graph and appends results.
        If no query is provided, retrieves recent episodes for context.

        Args:
            agent: An AG2 ConversableAgent with system_message and
                   update_system_message() attributes.
            query: Optional search query. If None, uses recent episodes.
            limit: Maximum number of results.
        """
        context_parts: list[str] = []

        if query:
            results = await self.search(query, limit=limit)
            if results:
                facts = [f"- {r['content']}" for r in results]
                context_parts.append("Knowledge graph context:\n" + "\n".join(facts))
        else:
            # Retrieve recent episodes for automatic context
            try:
                recent = await self._client.graph.episode.list(self._graph_uuid, page_size=2)
                episodes = recent.items or []
                if episodes:
                    episode_query = ""
                    for ep in episodes:
                        episode_query += f"{ep.content}\n"
                    episode_query = episode_query[-400:]

                    results = await self.search(episode_query, limit=limit)
                    if results:
                        facts = [f"- {r['content']}" for r in results]
                        context_parts.append("Knowledge graph context:\n" + "\n".join(facts))
            except Exception as e:
                logger.error("Zep graph.episode.list failed: %s", type(e).__name__)

        if context_parts:
            context = "\n\n".join(context_parts)
            original_msg = agent.system_message
            agent.update_system_message(f"{original_msg}\n\n## Knowledge Graph Context\n{context}")

    # Sync methods. They call the synchronous client.

    def search_sync(
        self,
        query: str,
        limit: int = 5,
        scope: str | None = "edges",
    ) -> list[dict[str, Any]]:
        """Synchronous form of :meth:`search`.

        Calls the synchronous ``sync_client`` directly.

        Raises:
            TypeError: If the manager has no synchronous ``sync_client``.
        """
        client = self._require_manager_sync_client()
        selected_scope = scope or "edges"
        try:
            if selected_scope == "edges":
                pager = client.graph.search_edges(self._graph_uuid, query=query, page_size=limit)
            elif selected_scope == "nodes":
                pager = client.graph.search_nodes(self._graph_uuid, query=query, page_size=limit)
            elif selected_scope == "episodes":
                pager = client.graph.search_episodes(self._graph_uuid, query=query, page_size=limit)
            else:
                logger.error("Unknown search scope: %s", selected_scope)
                return []
            return _search_results(selected_scope, pager.items)
        except Exception as e:
            logger.error("Zep graph search failed: %s", type(e).__name__)
            return []

    def add_data_sync(self, data: str, data_type: str = "text") -> bool:
        """Synchronous form of :meth:`add_data`.

        Calls the synchronous ``sync_client`` directly.

        Raises:
            TypeError: If the manager has no synchronous ``sync_client``.
        """
        client = self._require_manager_sync_client()
        try:
            client.graph.episode.add(
                self._graph_uuid,
                type=data_type,
                data=_truncate(data, GRAPH_MAX_CHARS, "graph data"),
            )
            return True
        except Exception as e:
            logger.error("Zep graph.episode.add failed: %s", type(e).__name__)
            return False


def _search_results(scope: str, items: list[Any] | None) -> list[dict[str, Any]]:
    """Convert the items of one search page to result dicts."""
    results: list[dict[str, Any]] = []
    for item in items or []:
        if scope == "edges":
            results.append(
                {
                    "content": item.fact,
                    "type": "edge",
                    "name": item.name,
                    "attributes": item.attributes or {},
                    "created_at": item.created_at,
                }
            )
        elif scope == "nodes":
            summary = item.summary or "No summary"
            results.append(
                {
                    "content": f"{item.name}: {summary}",
                    "type": "node",
                    "name": item.name,
                    "attributes": item.attributes or {},
                    "created_at": item.created_at,
                }
            )
        elif scope == "episodes":
            results.append(
                {
                    "content": item.content,
                    "type": "episode",
                    "source": item.source,
                    "role": item.role,
                    "created_at": item.created_at,
                }
            )
    return results
