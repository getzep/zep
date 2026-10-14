"""
Zep Graph Memory Manager for AG2.

This module provides the ZepGraphMemoryManager class for integrating Zep's
knowledge graph capabilities with AG2 agents.

This is used for named/shared knowledge graphs (identified by graph_uuid),
as opposed to user-scoped graphs managed by ZepMemoryManager.
"""

import logging
from typing import Any

from zep_cloud.client import AsyncZep

from zep_ag2.exceptions import ZepAG2ConfigError
from zep_ag2.tools import GRAPH_MAX_CHARS, _run_sync, _truncate

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
        >>> manager = ZepGraphMemoryManager(zep, graph_uuid="graph-uuid")
        >>> results = await manager.search("Python frameworks")
    """

    def __init__(
        self,
        client: AsyncZep,
        graph_uuid: str,
    ) -> None:
        """
        Initialize ZepGraphMemoryManager.

        Args:
            client: An initialized AsyncZep instance.
            graph_uuid: The knowledge graph UUID in Zep (required).

        Raises:
            ZepAG2ConfigError: If client is not an AsyncZep instance or graph_uuid is empty.
        """
        if not isinstance(client, AsyncZep):
            raise ZepAG2ConfigError("client must be an instance of AsyncZep")
        if not graph_uuid:
            raise ZepAG2ConfigError("graph_uuid is required")

        self._client = client
        self._graph_uuid = graph_uuid

    @property
    def client(self) -> AsyncZep:
        """The underlying AsyncZep client."""
        return self._client

    @property
    def graph_uuid(self) -> str:
        """The knowledge graph UUID."""
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

        Returns:
            A list of result dicts with 'content', 'type', and metadata fields.
        """
        try:
            method = {
                "edges": self._client.graph.search_edges,
                "nodes": self._client.graph.search_nodes,
                "episodes": self._client.graph.search_episodes,
            }.get(scope or "edges")
            if method is None:
                raise ValueError(f"Unsupported search scope: {scope}")
            search_results = await method(self._graph_uuid, query=query, page_size=limit)

            results: list[dict[str, Any]] = []

            if scope == "edges":
                for edge in search_results.items:
                    results.append(
                        {
                            "content": edge.fact,
                            "type": "edge",
                            "name": edge.name,
                            "attributes": edge.attributes or {},
                            "created_at": edge.created_at,
                        }
                    )

            if scope == "nodes":
                for node in search_results.items:
                    summary = node.summary or "No summary"
                    results.append(
                        {
                            "content": f"{node.name}: {summary}",
                            "type": "node",
                            "name": node.name,
                            "attributes": node.attributes or {},
                            "created_at": node.created_at,
                        }
                    )

            if scope == "episodes":
                for episode in search_results.items:
                    results.append(
                        {
                            "content": episode.content,
                            "type": "episode",
                            "source": episode.source,
                            "role": episode.role,
                            "created_at": episode.created_at,
                        }
                    )

            return results

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
                recent = await self._client.graph.episode.list(
                    self._graph_uuid, page_size=2, order="desc"
                )
                if recent.items:
                    episode_query = ""
                    for ep in recent.items:
                        episode_query += f"{ep.content or ''}\n"
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

    # Sync wrappers

    def search_sync(
        self,
        query: str,
        limit: int = 5,
        scope: str | None = "edges",
    ) -> list[dict[str, Any]]:
        """Synchronous wrapper for search().

        Bridges to async via the package's shared background event loop.
        """
        result = _run_sync(self.search(query, limit, scope))
        return list(result)

    def add_data_sync(self, data: str, data_type: str = "text") -> bool:
        """Synchronous wrapper for add_data().

        Bridges to async via the package's shared background event loop.
        """
        return bool(_run_sync(self.add_data(data, data_type)))
