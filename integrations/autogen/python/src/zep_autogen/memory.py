"""AutoGen memory implementations backed by Zep v4."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from autogen_core import CancellationToken
from autogen_core.memory import (
    Memory,
    MemoryContent,
    MemoryMimeType,
    MemoryQueryResult,
    UpdateContextResult,
)
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .limits import truncate_graph_data, truncate_message_content
from .tools import _typed_search_filters

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n"
    "{context}\n"
    "</ZEP_CONTEXT>"
)


@dataclass(frozen=True)
class ContextInput:
    """Inputs that a custom context builder can use."""

    zep: AsyncZep
    user_uuid: str
    thread_uuid: str | None
    graph_uuid: str
    user_message: str
    model_context: ChatCompletionContext


ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]


class ZepUserMemory(Memory):
    """Store a user's messages and inject the user's Zep context."""

    def __init__(
        self,
        client: AsyncZep,
        user_uuid: str,
        thread_uuid: str | None = None,
        *,
        graph_uuid: str | None = None,
        context_template_uuid: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
    ) -> None:
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")
        if not user_uuid:
            raise ValueError("user_uuid is required")
        if "{context}" not in context_template:
            raise ValueError("context_template must contain '{context}'")
        self._client = client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._graph_uuid = graph_uuid or user_uuid
        self._context_template_uuid = context_template_uuid
        self._context_builder = context_builder
        self._context_template = context_template

    @property
    def user_uuid(self) -> str:
        return self._user_uuid

    @property
    def thread_uuid(self) -> str | None:
        return self._thread_uuid

    @property
    def graph_uuid(self) -> str:
        return self._graph_uuid

    async def _ensure_thread(self) -> bool:
        if self._thread_uuid is not None:
            return True
        try:
            thread = await self._client.thread.create(user_uuid=self._user_uuid)
            self._thread_uuid = thread.uuid_
            if not self._thread_uuid:
                raise ValueError("Zep did not return a thread UUID")
            return True
        except Exception as exc:
            logger.warning("Failed to create Zep thread: %s", type(exc).__name__)
            return False

    async def add(
        self,
        content: MemoryContent,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """Persist a message to the thread or data to the user's graph."""
        supported = {MemoryMimeType.TEXT, MemoryMimeType.MARKDOWN, MemoryMimeType.JSON}
        if content.mime_type not in supported:
            raise ValueError(f"Unsupported mime type: {content.mime_type}")
        metadata = dict(content.metadata or {})
        content_type = metadata.get("type", "data")

        try:
            if content_type == "message":
                if not await self._ensure_thread():
                    return
                thread_uuid = self._thread_uuid
                if thread_uuid is None:
                    return
                role = str(metadata.get("role", "user"))
                message = Message(
                    name=metadata.get("name"),
                    content=truncate_message_content(str(content.content), label=role),
                    role=role,
                )
                await self._client.thread.add_messages(
                    thread_uuid=thread_uuid,
                    messages=[message],
                )
            elif content_type == "data":
                data_type = "json" if content.mime_type == MemoryMimeType.JSON else "text"
                await self._client.graph.episode.add(
                    self._graph_uuid,
                    type=data_type,
                    data=truncate_graph_data(str(content.content)),
                )
            else:
                raise ValueError(
                    f"Unsupported metadata type: {content_type}. Supported types: 'message', 'data'"
                )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Failed to add Zep memory: %s", type(exc).__name__)

    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """Search the user's graph and return AutoGen memory content."""
        query_text = str(query.content) if isinstance(query, MemoryContent) else query
        scope = kwargs.pop("scope", "edges")
        limit = kwargs.pop("limit", kwargs.pop("page_size", 5))
        search_filters = kwargs.pop("search_filters", kwargs.pop("filters", None))
        methods = {
            "edges": self._client.graph.search_edges,
            "nodes": self._client.graph.search_nodes,
            "episodes": self._client.graph.search_episodes,
            "observations": self._client.graph.search_observations,
            "thread_summaries": self._client.graph.search_thread_summaries,
        }
        try:
            if scope == "auto":
                context_kwargs: dict[str, Any] = {"query": query_text}
                if search_filters is not None:
                    context_kwargs["filters"] = search_filters
                result = await self._client.graph.get_context(
                    self._graph_uuid,
                    **context_kwargs,
                )
                context = getattr(result, "context", None)
                contents = (
                    [MemoryContent(content=context, mime_type=MemoryMimeType.TEXT)]
                    if context
                    else []
                )
            else:
                method = methods.get(scope)
                if method is None:
                    raise ValueError(f"Unsupported search scope: {scope}")
                search_kwargs: dict[str, Any] = {
                    "query": query_text,
                    "page_size": limit,
                    **kwargs,
                }
                if search_filters is not None:
                    search_kwargs["filters"] = _typed_search_filters(scope, search_filters)
                page = await method(self._graph_uuid, **search_kwargs)
                contents = self._results_to_memory_content(page.items or [], scope)
            return MemoryQueryResult(results=contents)
        except Exception as exc:
            logger.warning("Zep memory query failed: %s", type(exc).__name__)
            return MemoryQueryResult(results=[])

    @staticmethod
    def _results_to_memory_content(items: list[Any], scope: str) -> list[MemoryContent]:
        results: list[MemoryContent] = []
        for item in items:
            if scope == "edges":
                text = getattr(item, "fact", None)
                metadata = {
                    "source": "user_graph",
                    "edge_name": getattr(item, "name", None),
                    "edge_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                    "valid_at": getattr(item, "valid_at", None),
                    "invalid_at": getattr(item, "invalid_at", None),
                    "expired_at": getattr(item, "expired_at", None),
                }
            elif scope == "nodes":
                name = getattr(item, "name", None)
                summary = getattr(item, "summary", None)
                text = f"{name}:\n {summary}" if name and summary else name or summary
                metadata = {
                    "source": "user_graph",
                    "node_name": name,
                    "node_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                }
            elif scope == "episodes":
                text = getattr(item, "content", None)
                metadata = {
                    "source": "user_graph",
                    "episode_type": getattr(item, "source", None),
                    "episode_role": getattr(item, "role", None),
                    "episode_name": getattr(item, "role_name", None),
                    "created_at": getattr(item, "created_at", None),
                }
            elif scope == "observations":
                name = getattr(item, "name", None)
                summary = getattr(item, "summary", None)
                text = f"{name}: {summary}" if name and summary else name or summary
                metadata = {
                    "source": "user_graph",
                    "observation_name": name,
                    "observation_attributes": getattr(item, "attributes", None) or {},
                    "created_at": getattr(item, "created_at", None),
                }
            else:
                text = getattr(item, "summary", None) or getattr(item, "name", None)
                metadata = {
                    "source": "user_graph",
                    "thread_uuid": getattr(item, "thread_uuid", None),
                    "created_at": getattr(item, "created_at", None),
                }
            if text:
                results.append(
                    MemoryContent(
                        content=str(text),
                        mime_type=MemoryMimeType.TEXT,
                        metadata=metadata,
                    )
                )
        return results

    @staticmethod
    async def _last_user_text(model_context: ChatCompletionContext) -> str:
        for message in reversed(await model_context.get_messages()):
            if getattr(message, "type", None) == "UserMessage":
                content = getattr(message, "content", None)
                if isinstance(content, str):
                    return content
        return ""

    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """Retrieve and inject context. Retrieval errors do not fail an agent turn."""
        empty = UpdateContextResult(memories=MemoryQueryResult(results=[]))
        try:
            if not await model_context.get_messages():
                return empty
            memory_contents: list[MemoryContent] = []
            context_parts: list[str] = []
            if self._context_builder is not None:
                try:
                    built = await self._context_builder(
                        ContextInput(
                            zep=self._client,
                            user_uuid=self._user_uuid,
                            thread_uuid=self._thread_uuid,
                            graph_uuid=self._graph_uuid,
                            user_message=await self._last_user_text(model_context),
                            model_context=model_context,
                        )
                    )
                except Exception as exc:
                    logger.warning("Custom context_builder failed: %s", type(exc).__name__)
                    return empty
                if built:
                    memory_contents.append(
                        MemoryContent(
                            content=built,
                            mime_type=MemoryMimeType.TEXT,
                            metadata={"source": "context_builder"},
                        )
                    )
                    context_parts.append(self._context_template.replace("{context}", built))
            else:
                if not self._thread_uuid:
                    return empty
                thread_uuid = self._thread_uuid
                context_args: dict[str, Any] = {}
                if self._context_template_uuid is not None:
                    context_args["template_uuid"] = self._context_template_uuid
                context = await self._client.thread.get_context(thread_uuid, **context_args)
                context_text = getattr(context, "context", None)
                if context_text:
                    memory_contents.append(
                        MemoryContent(
                            content=context_text,
                            mime_type=MemoryMimeType.TEXT,
                            metadata={"source": "thread_context"},
                        )
                    )
                    context_parts.append(self._context_template.replace("{context}", context_text))
                    page = await self._client.thread.list_messages(
                        thread_uuid,
                        page_size=10,
                    )
                    messages = getattr(page, "items", None) or []
                    if messages:
                        history = [
                            f"{(message.name + ' ') if message.name else ''}{message.role}: {message.content}"
                            for message in messages
                        ]
                        context_parts.append("Recent conversation:\n" + "\n".join(history))
            if context_parts:
                await model_context.add_message(SystemMessage(content="\n\n".join(context_parts)))
            return UpdateContextResult(memories=MemoryQueryResult(results=memory_contents))
        except Exception as exc:
            logger.warning("Zep context retrieval failed: %s", type(exc).__name__)
            return empty

    async def clear(self) -> None:
        if self._thread_uuid:
            await self._client.thread.delete(self._thread_uuid)

    async def close(self) -> None:
        """The caller owns the client lifecycle."""
