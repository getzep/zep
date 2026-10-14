"""Context helpers for the CrewAI integration."""

import logging

from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n<ZEP_CONTEXT>\n{context}\n</ZEP_CONTEXT>"
)


def search_graph_and_compose_context(
    client: Zep,
    query: str,
    graph_uuid: str,
    search_filters: SearchFilters | None = None,
    context_template: str = DEFAULT_CONTEXT_TEMPLATE,
    template_uuid: str | None = None,
    max_characters: int | None = None,
) -> str | None:
    """Get Zep's automatically selected graph context and apply a template."""
    try:
        context_args: dict[str, object] = {"query": query[:400]}
        optional = {
            "filters": search_filters,
            "template_uuid": template_uuid,
            "max_characters": max_characters,
        }
        context_args.update({key: value for key, value in optional.items() if value is not None})
        response = client.graph.get_context(graph_uuid, **context_args)
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to retrieve graph context: %s", exc)
        return None
    context = getattr(response, "context", None)
    if not context:
        return None
    return context_template.replace("{context}", context)
