"""
Zep AutoGen Tools.

This module provides AutoGen tools for interacting with Zep memory storage,
including graph and user memory operations.
"""

import logging
from typing import Annotated, Any

from autogen_core.tools import FunctionTool
from zep_cloud.client import AsyncZep

logger = logging.getLogger(__name__)


async def search_memory(
    client: AsyncZep,
    query: Annotated[str, "The search query to find relevant memories"],
    graph_id: Annotated[str | None, "Graph ID to search in (for generic knowledge graph)"] = None,
    user_id: Annotated[str | None, "User ID to search graph for (for user knowledge graph)"] = None,
    limit: Annotated[int, "Maximum number of results to return"] = 10,
    scope: Annotated[
        str | None,
        "Scope of search: 'edges' (facts), 'nodes' (entities), 'episodes' (for knowledge graph). Defaults to edges",
    ] = "edges",
) -> list[dict[str, Any]]:
    """
    Search Zep memory storage for relevant information.

    Searches either graph memory (if graph_id provided) or user memory (if user_id provided).
    Exactly one of graph_id or user_id must be provided.

    Args:
        client: AsyncZep client instance
        query: Search query string
        graph_id: Graph ID for graph memory search
        user_id: User ID for user memory search
        limit: Maximum results to return
        scope: Optional search filters

    Returns:
        List of memory results with content and metadata

    Raises:
        ValueError: If neither or both graph_id and user_id are provided
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided")

    if graph_id and user_id:
        raise ValueError("Only one of graph_id or user_id should be provided")

    try:
        results = []
        if graph_id:
            # Search graph memory
            search_results = await client.graph.search(
                graph_id=graph_id, query=query, limit=limit, scope=scope
            )

        else:  # user_id provided
            # Search user memory
            search_results = await client.graph.search(user_id=user_id, query=query, limit=limit)

        # Process graph results
        if search_results.edges:
            for edge in search_results.edges:
                results.append(
                    {
                        "content": edge.fact,
                        "type": "edge",
                        "name": edge.name,
                        "attributes": edge.attributes or {},
                        "created_at": edge.created_at,
                        "valid_at": edge.valid_at,
                        "invalid_at": edge.invalid_at,
                        "expired_at": edge.expired_at,
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

        logger.info(f"Found {len(results)} memories for query: {query}")
        return results

    except Exception as e:
        logger.error(f"Error searching memory: {e}")
        return []


async def add_graph_data(
    client: AsyncZep,
    data: Annotated[str, "The data/information to store in the graph"],
    graph_id: Annotated[str | None, "Graph ID to store data in (for graph memory)"] = None,
    user_id: Annotated[str | None, "User ID to store data for (for user memory)"] = None,
    data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
) -> dict[str, Any]:
    """
    Add data to Zep memory storage.

    Adds data to either graph memory (if graph_id provided) or user memory (if user_id provided).

    Args:
        client: AsyncZep client instance
        data: Data content to store
        graph_id: Graph ID for non-user graph storage
        user_id: User ID for user graph storage
        data_type: Type of data being stored

    Returns:
        Dictionary with operation result

    Raises:
        ValueError: If parameters are invalid
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided")

    if graph_id and user_id:
        raise ValueError("Only one of graph_id or user_id should be provided")

    try:
        if graph_id:
            # Add to graph memory
            await client.graph.add(graph_id=graph_id, type=data_type, data=data)

            logger.debug(f"Added data to graph {graph_id}")
            return {
                "success": True,
                "message": "Data added to graph memory",
                "graph_id": graph_id,
                "data_type": data_type,
            }

        else:  # user_id provided
            # Add to user graph memory
            await client.graph.add(user_id=user_id, type=data_type, data=data)

            logger.debug(f"Added data to user graph {user_id}")
            return {
                "success": True,
                "message": "Data added to user graph memory",
                "user_id": user_id,
                "data_type": data_type,
            }

    except Exception as e:
        logger.error(f"Error adding memory data: {e}")
        return {"success": False, "message": f"Failed to add data: {str(e)}"}


def create_search_graph_tool(
    client: AsyncZep, graph_id: str | None = None, user_id: str | None = None
) -> FunctionTool:
    """
    Create a search memory tool bound to a Zep client.

    Args:
        client: AsyncZep client instance
        graph_id: Optional graph ID to bind to this tool
        user_id: Optional user ID to bind to this tool

    Returns:
        FunctionTool for searching memory

    Raises:
        ValueError: If neither or both graph_id and user_id are provided
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided when creating the tool")

    if graph_id and user_id:
        raise ValueError(
            "Only one of graph_id or user_id should be provided when creating the tool"
        )

    async def bound_search_memory(
        query: Annotated[str, "The search query to find relevant memories"],
        limit: Annotated[int, "Maximum number of results to return"] = 10,
        scope: Annotated[
            str | None,
            "Scope of search: 'edges' (facts), 'nodes' (entities), 'episodes' (for knowledge graph). Defaults to edges",
        ] = "edges",
    ) -> list[dict[str, Any]]:
        return await search_memory(client, query, graph_id, user_id, limit, scope)

    return FunctionTool(
        bound_search_memory,
        description=f"Search Zep memory storage for relevant information in {'graph ' + (graph_id or '') if graph_id else 'user ' + (user_id or '')}.",
    )


def create_add_graph_data_tool(
    client: AsyncZep, graph_id: str | None = None, user_id: str | None = None
) -> FunctionTool:
    """
    Create an add memory data tool bound to a Zep client.

    Args:
        client: AsyncZep client instance
        graph_id: Optional graph ID to bind to this tool
        user_id: Optional user ID to bind to this tool

    Returns:
        FunctionTool for adding memory data

    Raises:
        ValueError: If neither or both graph_id and user_id are provided
    """
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided when creating the tool")

    if graph_id and user_id:
        raise ValueError(
            "Only one of graph_id or user_id should be provided when creating the tool"
        )

    async def bound_add_memory_data(
        data: Annotated[str, "The data/information to store in memory"],
        data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
    ) -> dict[str, Any]:
        return await add_graph_data(client, data, graph_id, user_id, data_type)

    return FunctionTool(
        bound_add_memory_data,
        description=f"Add data to Zep memory storage in {'graph ' + (graph_id or '') if graph_id else 'user ' + (user_id or '')}.",
    )
