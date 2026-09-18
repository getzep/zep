"""
Utility functions for Zep CrewAI integration.
"""

import logging
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

#: Default template used to wrap a composed context string before it is
#: returned to the caller for agent consumption. Rendered via plain string
#: replacement (``template.replace("{context}", context_text)``), never
#: ``str.format`` -- so context text containing ``{``/``}``/``%`` is always
#: safe to inject.
#:
#: This exact string is canonical across zep-adk's Python, Go, and
#: TypeScript implementations -- keep them in sync. This is the single
#: canonical definition; :mod:`zep_crewai.user_storage` and the package
#: root re-export it.
DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n"
    "{context}\n"
    "</ZEP_CONTEXT>"
)


def compose_graph_context(
    client: Zep,
    query: str,
    graph_uuid: str,
    max_characters: int | None = None,
    search_filters: SearchFilters | None = None,
    context_template: str = DEFAULT_CONTEXT_TEMPLATE,
) -> str | None:
    """
    Retrieve a Context Block for a graph and wrap it in ``context_template``.

    Zep v4 assembles the Context Block on the server. ``graph.get_context``
    returns a prompt-ready string for the graph that ``graph_uuid`` names.
    The v3 client-side composition helper (``compose_context_string``) does
    not exist in the v4 SDK.

    Args:
        client: Zep client instance.
        query: Search query string. Zep rejects a query above 400 characters,
            so a longer query is truncated.
        graph_uuid: The UUID of the graph. For a user graph, pass the
            ``graph_uuid`` of the user.
        max_characters: Optional maximum length of the Context Block.
        search_filters: Optional search filters.
        context_template: Template used to wrap the Context Block. The
            template must contain a literal ``{context}`` placeholder.
            Defaults to :data:`DEFAULT_CONTEXT_TEMPLATE`.

    Returns:
        The Context Block, wrapped in ``context_template``, or ``None`` when
        the graph returns no context.
    """
    logger = logging.getLogger(__name__)

    if not graph_uuid:
        raise ValueError("graph_uuid must be provided")

    truncated_query = query[:400] if len(query) > 400 else query

    kwargs: dict[str, Any] = {"query": truncated_query}
    if search_filters is not None:
        kwargs["filters"] = search_filters
    if max_characters is not None:
        kwargs["max_characters"] = max_characters

    try:
        response = client.graph.get_context(graph_uuid, **kwargs)
    except Exception as e:
        logger.error(f"Failed to get context from graph: {e}")
        return None

    context = response.context
    if not context:
        return None

    return context_template.replace("{context}", context)
