"""
Zep CrewAI Tools.

This module provides CrewAI tools for interacting with Zep memory storage,
including graph and user memory operations.
"""

import logging
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from zep_cloud.client import Zep

logger = logging.getLogger(__name__)


class SearchMemoryInput(BaseModel):
    """Input schema for memory search tool."""

    query: str = Field(..., description="The search query to find relevant memories")
    limit: int = Field(default=10, description="Maximum number of results to return")
    scope: str = Field(
        default="edges",
        description="Scope of search: 'edges' (facts), 'nodes' (entities), 'episodes', or 'all'",
    )


class AddGraphDataInput(BaseModel):
    """Input schema for adding data to graph."""

    data: str = Field(..., description="The data/information to store in the graph")
    data_type: str = Field(default="text", description="Type of data: 'text', 'json', or 'message'")


class ZepSearchTool(BaseTool):
    """
    Tool for searching Zep memory storage.

    Can search either graph memory or user memory depending on initialization.
    """

    name: str = "Zep Memory Search"
    description: str = "Search Zep memory storage for relevant information"
    args_schema: type[BaseModel] = SearchMemoryInput

    def __init__(
        self, client: Zep, graph_id: str | None = None, user_id: str | None = None, **kwargs: Any
    ):
        """
        Initialize search tool bound to either a graph or user.

        Args:
            client: Zep client instance
            graph_id: Graph ID for generic knowledge graph search
            user_id: User ID for user-specific graph search
            **kwargs: Additional configuration
        """
        if not graph_id and not user_id:
            raise ValueError("Either graph_id or user_id must be provided")

        if graph_id and user_id:
            raise ValueError("Only one of graph_id or user_id should be provided")

        # Update description based on target
        if graph_id:
            kwargs["description"] = f"Search Zep graph '{graph_id}' for relevant information"
        else:
            kwargs["description"] = f"Search user '{user_id}' memories for relevant information"

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_id = graph_id
        self._user_id = user_id

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_id(self) -> str | None:
        """Get the graph ID."""
        return self._graph_id

    @property
    def user_id(self) -> str | None:
        """Get the user ID."""
        return self._user_id

    def _run(self, query: str, limit: int = 10, scope: str = "edges") -> str:
        """
        Execute the search operation.

        Args:
            query: Search query
            limit: Maximum results
            scope: Search scope

        Returns:
            Formatted search results
        """
        try:
            results = []

            if scope == "all":
                # Search all scopes
                scopes = ["edges", "nodes", "episodes"]
            else:
                scopes = [scope]

            for search_scope in scopes:
                if self._graph_id:
                    # Search graph memory
                    search_results = self._client.graph.search(
                        graph_id=self._graph_id, query=query, limit=limit, scope=search_scope
                    )
                else:
                    # Search user memory
                    search_results = self._client.graph.search(
                        user_id=self._user_id, query=query, limit=limit, scope=search_scope
                    )

                # Process results based on scope
                if search_scope == "edges" and search_results.edges:
                    for edge in search_results.edges:
                        results.append(
                            {
                                "type": "fact",
                                "content": edge.fact,
                                "name": edge.name,
                                "created_at": str(edge.created_at) if edge.created_at else None,
                            }
                        )

                elif search_scope == "nodes" and search_results.nodes:
                    for node in search_results.nodes:
                        results.append(
                            {
                                "type": "entity",
                                "content": f"{node.name}: {node.summary}",
                                "name": node.name,
                                "created_at": str(node.created_at) if node.created_at else None,
                            }
                        )

                elif search_scope == "episodes" and search_results.episodes:
                    for episode in search_results.episodes:
                        results.append(
                            {
                                "type": "episode",
                                "content": episode.content,
                                "source": episode.source,
                                "role": episode.role,
                                "created_at": str(episode.created_at)
                                if episode.created_at
                                else None,
                            }
                        )

            if not results:
                return f"No results found for query: '{query}'"

            # Format results for agent consumption
            formatted = f"Found {len(results)} relevant memories:\n\n"
            for i, result in enumerate(results, 1):
                result_type = result.get("type", "unknown")
                formatted += f"{i}. [{result_type.upper() if result_type else 'UNKNOWN'}] {result['content']}\n"
                if result.get("created_at"):
                    formatted += f"   (Created: {result['created_at']})\n"
                formatted += "\n"

            logger.info(f"Found {len(results)} memories for query: {query}")
            return formatted

        except Exception as e:
            error_msg = f"Error searching Zep memory: {str(e)}"
            logger.error(error_msg)
            return error_msg


class ZepAddDataTool(BaseTool):
    """
    Tool for adding data to Zep memory storage.

    Can add data to either graph memory or user memory depending on initialization.
    """

    name: str = "Zep Add Data"
    description: str = "Add data to Zep memory storage"
    args_schema: type[BaseModel] = AddGraphDataInput

    def __init__(
        self, client: Zep, graph_id: str | None = None, user_id: str | None = None, **kwargs: Any
    ):
        """
        Initialize add data tool bound to either a graph or user.

        Args:
            client: Zep client instance
            graph_id: Graph ID for generic knowledge graph
            user_id: User ID for user-specific graph
            **kwargs: Additional configuration
        """
        if not graph_id and not user_id:
            raise ValueError("Either graph_id or user_id must be provided")

        if graph_id and user_id:
            raise ValueError("Only one of graph_id or user_id should be provided")

        # Update description based on target
        if graph_id:
            kwargs["description"] = f"Add data to Zep graph '{graph_id}'"
        else:
            kwargs["description"] = f"Add data to user '{user_id}' memory"

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_id = graph_id
        self._user_id = user_id

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_id(self) -> str | None:
        """Get the graph ID."""
        return self._graph_id

    @property
    def user_id(self) -> str | None:
        """Get the user ID."""
        return self._user_id

    def _run(self, data: str, data_type: str = "text") -> str:
        """
        Execute the add data operation.

        Args:
            data: Data to store
            data_type: Type of data

        Returns:
            Success or error message
        """
        try:
            # Validate data type
            if data_type not in ["text", "json", "message"]:
                data_type = "text"

            if self._graph_id:
                # Add to graph memory
                self._client.graph.add(graph_id=self._graph_id, type=data_type, data=data)

                success_msg = f"Successfully added {data_type} data to graph '{self._graph_id}'"
                logger.debug(f"Added data to graph {self._graph_id}: {data[:100]}...")

            else:
                # Add to user graph memory
                self._client.graph.add(user_id=self._user_id, type=data_type, data=data)

                success_msg = (
                    f"Successfully added {data_type} data to user '{self._user_id}' memory"
                )
                logger.debug(f"Added data to user {self._user_id}: {data[:100]}...")

            return success_msg

        except Exception as e:
            error_msg = f"Error adding data to Zep: {str(e)}"
            logger.error(error_msg)
            return error_msg


def create_search_tool(
    client: Zep, graph_id: str | None = None, user_id: str | None = None
) -> ZepSearchTool:
    """
    Create a search tool bound to a Zep client.

    Args:
        client: Zep client instance
        graph_id: Optional graph ID for generic knowledge graph
        user_id: Optional user ID for user-specific graph

    Returns:
        ZepSearchTool instance

    Raises:
        ValueError: If neither or both IDs are provided
    """
    return ZepSearchTool(client=client, graph_id=graph_id, user_id=user_id)


def create_add_data_tool(
    client: Zep, graph_id: str | None = None, user_id: str | None = None
) -> ZepAddDataTool:
    """
    Create an add data tool bound to a Zep client.

    Args:
        client: Zep client instance
        graph_id: Optional graph ID for generic knowledge graph
        user_id: Optional user ID for user-specific graph

    Returns:
        ZepAddDataTool instance

    Raises:
        ValueError: If neither or both IDs are provided
    """
    return ZepAddDataTool(client=client, graph_id=graph_id, user_id=user_id)
