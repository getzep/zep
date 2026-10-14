"""Storage adapter for a shared Zep graph."""

import logging
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

from .limits import truncate_graph_data
from .utils import DEFAULT_CONTEXT_TEMPLATE, search_graph_and_compose_context


class ZepGraphStorage:
    """Store data in and retrieve context from a Zep graph."""

    def __init__(
        self,
        client: Zep,
        graph_uuid: str,
        search_filters: SearchFilters | None = None,
        *,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        template_uuid: str | None = None,
        max_characters: int | None = None,
    ) -> None:
        if not isinstance(client, Zep):
            raise TypeError("client must be an instance of Zep")
        if not graph_uuid:
            raise ValueError("graph_uuid is required")
        self._client = client
        self._graph_uuid = graph_uuid
        self._search_filters = search_filters
        self._context_template = context_template
        self._template_uuid = template_uuid
        self._max_characters = max_characters
        self._logger = logging.getLogger(__name__)

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        metadata = metadata or {}
        kind = metadata.get("type", "text")
        if kind not in {"text", "json", "message"}:
            kind = "text"
        try:
            self._client.graph.episode.add(
                self._graph_uuid,
                data=truncate_graph_data(str(value)),
                type=kind,
                metadata=metadata,
            )
        except Exception as exc:
            self._logger.error("Error saving to Zep graph: %s", exc)

    def search(
        self, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> list[dict[str, Any]]:
        context = search_graph_and_compose_context(
            self._client,
            query,
            self._graph_uuid,
            search_filters=self._search_filters,
            context_template=self._context_template,
            template_uuid=self._template_uuid,
            max_characters=self._max_characters,
        )
        if not context:
            return []
        return [{"context": context, "type": "graph_context", "source": "graph", "query": query}]

    def reset(self) -> None:
        """Keep graph data because other applications may use it."""

    @property
    def graph_uuid(self) -> str:
        return self._graph_uuid
