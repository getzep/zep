"""User and thread storage adapters for CrewAI."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from zep_cloud.client import Zep
from zep_cloud.types import AddMessage, SearchFilters

from .limits import truncate_graph_data, truncate_message_content
from .utils import DEFAULT_CONTEXT_TEMPLATE, search_graph_and_compose_context

__all__ = ["ZepUserStorage", "ContextInput", "ContextBuilder", "DEFAULT_CONTEXT_TEMPLATE"]


@dataclass(frozen=True)
class ContextInput:
    """Inputs available to a custom context builder."""

    zep: Zep
    user_uuid: str
    thread_uuid: str
    graph_uuid: str
    user_message: str


ContextBuilder = Callable[[ContextInput], str | None]


class ZepUserStorage:
    """Persist conversation messages and retrieve user graph context."""

    def __init__(
        self,
        client: Zep,
        user_uuid: str,
        thread_uuid: str,
        graph_uuid: str | None = None,
        search_filters: SearchFilters | None = None,
        *,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        template_uuid: str | None = None,
        max_characters: int | None = None,
        mode: Literal["summary", "basic"] | None = None,
    ) -> None:
        if not isinstance(client, Zep):
            raise TypeError("client must be an instance of Zep")
        if not user_uuid or not thread_uuid:
            raise ValueError("user_uuid and thread_uuid are required")
        self._client = client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._graph_uuid = graph_uuid or client.user.get(user_uuid).graph_uuid
        self._search_filters = search_filters
        self._context_builder = context_builder
        self._context_template = context_template
        self._template_uuid = template_uuid
        self._max_characters = max_characters
        self._logger = logging.getLogger(__name__)
        if mode is not None:
            warnings.warn("'mode' is deprecated and ignored.", DeprecationWarning, stacklevel=2)

    def save(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        metadata = metadata or {}
        content = str(value)
        kind = metadata.get("type", "text")
        if kind not in {"message", "json", "text"}:
            kind = "text"
        try:
            if kind == "message":
                self._client.thread.add_messages(
                    self._thread_uuid,
                    messages=[
                        AddMessage(
                            role=metadata.get("role", "user"),
                            name=metadata.get("name"),
                            content=truncate_message_content(content),
                        )
                    ],
                )
            else:
                self._client.graph.episode.add(
                    self._graph_uuid,
                    data=truncate_graph_data(content),
                    type=kind,
                    metadata=metadata,
                )
        except Exception as exc:
            self._logger.error("Error saving to Zep user storage: %s", exc)

    def search(
        self, query: str, limit: int = 10, score_threshold: float = 0.0
    ) -> list[dict[str, Any]]:
        try:
            if self._context_builder is None:
                context = search_graph_and_compose_context(
                    self._client,
                    query,
                    self._graph_uuid,
                    search_filters=self._search_filters,
                    context_template=self._context_template,
                    template_uuid=self._template_uuid,
                    max_characters=self._max_characters,
                )
            else:
                built = self._context_builder(
                    ContextInput(
                        zep=self._client,
                        user_uuid=self._user_uuid,
                        thread_uuid=self._thread_uuid,
                        graph_uuid=self._graph_uuid,
                        user_message=query,
                    )
                )
                context = self._context_template.replace("{context}", built) if built else None
        except Exception as exc:
            self._logger.warning("Context retrieval failed: %s", exc)
            return []
        if not context:
            return []
        return [
            {
                "context": context,
                "type": "user_graph_context",
                "source": "user_graph",
                "query": query,
            }
        ]

    def get_context(self) -> str | None:
        try:
            context_args = (
                {"template_uuid": self._template_uuid} if self._template_uuid is not None else {}
            )
            response = self._client.thread.get_context(self._thread_uuid, **context_args)
            return getattr(response, "context", None)
        except Exception as exc:
            self._logger.error("Error getting thread context: %s", exc)
            return None

    def reset(self) -> None:
        """Keep Zep memories because they persist across crew runs."""

    @property
    def user_uuid(self) -> str:
        return self._user_uuid

    @property
    def thread_uuid(self) -> str:
        return self._thread_uuid

    @property
    def graph_uuid(self) -> str:
        return self._graph_uuid
