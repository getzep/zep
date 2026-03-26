"""
Zep Graph Memory Manager for AG2.

This module provides the ZepGraphMemoryManager class for integrating Zep's
knowledge graph capabilities with AG2 agents.

This is used for named/shared knowledge graphs (identified by graph_id),
as opposed to user-scoped graphs managed by ZepMemoryManager.
"""

import asyncio
import logging
from typing import Any

from zep_cloud.client import AsyncZep

from zep_ag2.exceptions import ZepAG2ConfigError

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
        >>> manager = ZepGraphMemoryManager(zep, graph_id="company_kb")
        >>> results = await manager.search("Python frameworks")
    """

    def __init__(
        self,
        client: AsyncZep,
        graph_id: str,
    ) -> None:
        """
        Initialize ZepGraphMemoryManager.

        Args:
            client: An initialized AsyncZep instance.
            graph_id: The knowledge graph identifier in Zep (required).

        Raises:
            ZepAG2ConfigError: If client is not an AsyncZep instance or graph_id is empty.
        """
        if not isinstance(client, AsyncZep):
            raise ZepAG2ConfigError("client must be an instance of AsyncZep")
        if not graph_id:
            raise ZepAG2ConfigError("graph_id is required")

        self._client = client
        self._graph_id = graph_id

    @property
    def client(self) -> AsyncZep:
        """The underlying AsyncZep client."""
        return self._client

    @property
    def graph_id(self) -> str:
        """The knowledge graph identifier."""
        return self._graph_id

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
            search_results = await self._client.graph.search(
                graph_id=self._graph_id,
                query=query,
                limit=limit,
                scope=scope,
            )

            results: list[dict[str, Any]] = []

            if search_results.edges:
                for edge in search_results.edges:
                    results.append(
                        {
                            "content": edge.fact,
                            "type": "edge",
                            "name": edge.name,
                            "attributes": edge.attributes or {},
                            "created_at": edge.created_at,
                        }
                    )

            if search_results.nodes:
                for node in search_results.nodes:
                    results.append(
                        {
                            "content": f"{node.name}: {node.summary}",
                            "type": "node",
                            "name": node.name,
                            "attributes": node.attributes or {},
                            "created_at": node.created_at,
                        }
                    )

            if search_results.episodes:
                for episode in search_results.episodes:
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
            logger.error(f"Error searching graph: {e}")
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
            await self._client.graph.add(
                graph_id=self._graph_id,
                type=data_type,
                data=data,
            )
            return True
        except Exception as e:
            logger.error(f"Error adding graph data: {e}")
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
                recent = await self._client.graph.episode.get_by_graph_id(
                    graph_id=self._graph_id, lastn=2
                )
                if recent.episodes:
                    episode_query = ""
                    for ep in recent.episodes:
                        episode_query += f"{ep.content}\n"
                    episode_query = episode_query[-400:]

                    results = await self.search(episode_query, limit=limit)
                    if results:
                        facts = [f"- {r['content']}" for r in results]
                        context_parts.append("Knowledge graph context:\n" + "\n".join(facts))
            except Exception as e:
                logger.error(f"Error retrieving graph context: {e}")

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
        """Synchronous wrapper for search()."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, self.search(query, limit, scope)).result()
        return loop.run_until_complete(self.search(query, limit, scope))

    def add_data_sync(self, data: str, data_type: str = "text") -> bool:
        """Synchronous wrapper for add_data()."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, self.add_data(data, data_type)).result()
        return loop.run_until_complete(self.add_data(data, data_type))
