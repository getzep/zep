"""Compatibility adapter for applications that use the historical ZepStorage API."""

from __future__ import annotations

from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

from .user_storage import DEFAULT_CONTEXT_TEMPLATE, ContextBuilder, ZepUserStorage


class ZepStorage:
    """Expose ``save``, ``search``, and ``reset`` over user memory."""

    def __init__(
        self,
        client: Zep,
        user_uuid: str,
        thread_uuid: str,
        graph_uuid: str | None = None,
        search_filters: SearchFilters | None = None,
        *,
        context_builder: ContextBuilder | None = None,
        context_template: str | None = None,
        template_uuid: str | None = None,
    ) -> None:
        self._storage = ZepUserStorage(
            client=client,
            user_uuid=user_uuid,
            thread_uuid=thread_uuid,
            graph_uuid=graph_uuid,
            search_filters=search_filters,
            context_builder=context_builder,
            context_template=(
                context_template if context_template is not None else DEFAULT_CONTEXT_TEMPLATE
            ),
            template_uuid=template_uuid,
        )

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        self._storage.save(value, metadata)

    def search(
        self, query: str, limit: int = 5, score_threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        return self._storage.search(query, limit, score_threshold)

    def reset(self) -> None:
        self._storage.reset()

    @property
    def user_uuid(self) -> str:
        return self._storage.user_uuid

    @property
    def thread_uuid(self) -> str:
        return self._storage.thread_uuid

    @property
    def graph_uuid(self) -> str:
        return self._storage.graph_uuid
