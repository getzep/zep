"""
Utility functions for Zep CrewAI integration.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from zep_cloud.client import Zep
from zep_cloud.graph.utils import compose_context_string
from zep_cloud.types import SearchFilters


def search_graph_and_compose_context(
    client: Zep,
    query: str,
    graph_id: str | None = None,
    user_id: str | None = None,
    facts_limit: int = 20,
    entity_limit: int = 5,
    episodes_limit: int = 10,
    search_filters: SearchFilters | None = None,
) -> str | None:
    """
    Perform parallel graph searches and compose context string.

    Searches for edges, nodes, and episodes in parallel, then uses
    compose_context_string to format the results.

    Args:
        client: Zep client instance
        query: Search query string
        graph_id: Graph ID for generic graph search
        user_id: User ID for user graph search
        facts_limit: Maximum number of facts (edges) to retrieve
        entity_limit: Maximum number of entities (nodes) to retrieve
        episodes_limit: Maximum number of episodes to retrieve
        search_filters: Optional search filters

    Returns:
        Composed context string or None if no results
    """
    logger = logging.getLogger(__name__)

    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided")

    # Truncate query if too long
    truncated_query = query[:400] if len(query) > 400 else query

    edges = []
    nodes = []
    episodes = []

    # Execute searches in parallel
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            # Search for facts (edges)
            if graph_id:
                future_edges = executor.submit(
                    client.graph.search,
                    graph_id=graph_id,
                    query=truncated_query,
                    limit=facts_limit,
                    scope="edges",
                    search_filters=search_filters,
                )
            else:
                future_edges = executor.submit(
                    client.graph.search,
                    user_id=user_id,
                    query=truncated_query,
                    limit=facts_limit,
                    scope="edges",
                    search_filters=search_filters,
                )

            # Search for entities (nodes)
            if graph_id:
                future_nodes = executor.submit(
                    client.graph.search,
                    graph_id=graph_id,
                    query=truncated_query,
                    limit=entity_limit,
                    scope="nodes",
                    search_filters=search_filters,
                )
            else:
                future_nodes = executor.submit(
                    client.graph.search,
                    user_id=user_id,
                    query=truncated_query,
                    limit=entity_limit,
                    scope="nodes",
                    search_filters=search_filters,
                )

            # Search for episodes
            if graph_id:
                future_episodes = executor.submit(
                    client.graph.search,
                    graph_id=graph_id,
                    query=truncated_query,
                    limit=episodes_limit,
                    scope="episodes",
                    search_filters=search_filters,
                )
            else:
                future_episodes = executor.submit(
                    client.graph.search,
                    user_id=user_id,
                    query=truncated_query,
                    limit=episodes_limit,
                    scope="episodes",
                    search_filters=search_filters,
                )

            edge_results = future_edges.result()
            node_results = future_nodes.result()
            episode_results = future_episodes.result()

            if edge_results and edge_results.edges:
                edges = edge_results.edges

            if node_results and node_results.nodes:
                nodes = node_results.nodes

            if episode_results and episode_results.episodes:
                episodes = episode_results.episodes

    except Exception as e:
        logger.error(f"Failed to search graph: {e}")
        return None

    # Compose context string from all results
    if edges or nodes or episodes:
        context = compose_context_string(edges=edges, nodes=nodes, episodes=episodes)
        return context

    return None
