from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from livekit import agents
from livekit.agents.llm.chat_context import ChatContext, ChatMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .exceptions import AgentConfigurationError
from .limits import truncate_graph_data, truncate_message_content

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_TEMPLATE = (
    "The following context is retrieved from Zep, the agent's long-term memory. "
    "It contains relevant facts, entities, and prior knowledge about the user. "
    "Use it to inform your responses.\n\n"
    "<ZEP_CONTEXT>\n"
    "{context}\n"
    "</ZEP_CONTEXT>"
)


def _current_session(agent: agents.Agent) -> Any | None:
    try:
        return agent.session
    except RuntimeError:
        return None


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.text if hasattr(item, "text") else item
            for item in content
            if isinstance(item, str) or hasattr(item, "text")
        )
    return str(content)


def _render_context(template: str, context: str) -> str:
    if "{context}" not in template:
        raise AgentConfigurationError("context_template must contain '{context}'")
    return template.replace("{context}", context)


@dataclass(frozen=True)
class ContextInput:
    zep: AsyncZep
    user_uuid: str
    thread_uuid: str
    user_message: str
    session: Any | None = None


ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


@dataclass(frozen=True)
class GraphContextInput:
    zep: AsyncZep
    graph_uuid: str
    user_message: str
    session: Any | None = None


GraphContextBuilder = Callable[[GraphContextInput], Awaitable[str | None]]


class _ZepAgent(agents.Agent):
    def _setup_session_handlers(self) -> None:
        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event: Any) -> None:
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event: Any) -> None:
        try:
            item = getattr(event, "item", None)
            if item is None or getattr(item, "role", None) != "assistant":
                return
            content = _extract_text_content(getattr(item, "content", ""))
            if content.strip():
                await self._store_assistant_message(content.strip(), item)
        except Exception:
            logger.warning("Failed to store the assistant message in Zep", exc_info=True)

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        raise NotImplementedError

    async def on_enter(self) -> None:
        await super().on_enter()
        if hasattr(self, "session"):
            self._setup_session_handlers()


class ZepUserAgent(_ZepAgent):
    """LiveKit agent with thread-based Zep memory."""

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str,
        thread_uuid: str,
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not user_uuid:
            raise AgentConfigurationError("user_uuid must be a non-empty string")
        if not thread_uuid:
            raise AgentConfigurationError("thread_uuid must be a non-empty string")
        _render_context(context_template, "")
        super().__init__(**kwargs)
        self._zep_client = zep_client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._user_message_name = user_message_name
        self._assistant_message_name = assistant_message_name
        self._context_builder = context_builder
        self._context_template = context_template

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        try:
            message = Message(
                content=truncate_message_content(content_text, label="assistant"),
                role="assistant",
                name=self._assistant_message_name or getattr(item, "name", None),
            )
            await self._zep_client.thread.add_messages(
                self._thread_uuid,
                messages=[message],
            )
        except Exception:
            logger.warning("Failed to store the assistant response in Zep", exc_info=True)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        await super().on_user_turn_completed(turn_ctx, new_message)
        user_text = (new_message.text_content or "").strip()
        if not user_text:
            return
        user_text = truncate_message_content(user_text, label="user")

        if self._context_builder is None:
            context = await self._persist_with_context(user_text)
        else:
            context = await self._persist_and_build_context(user_text)
        if context:
            turn_ctx.add_message(
                role="system",
                content=_render_context(self._context_template, context),
            )

    async def _persist_with_context(self, user_text: str) -> str | None:
        try:
            response = await self._zep_client.thread.add_messages(
                self._thread_uuid,
                messages=[
                    Message(
                        content=user_text,
                        role="user",
                        name=self._user_message_name,
                    )
                ],
                return_context=True,
            )
            return response.context if response else None
        except Exception:
            logger.warning("Failed to persist the user turn or retrieve context", exc_info=True)
            return None

    async def _persist_and_build_context(self, user_text: str) -> str | None:
        async def persist() -> None:
            message = Message(content=user_text, role="user", name=self._user_message_name)
            await self._zep_client.thread.add_messages(self._thread_uuid, messages=[message])

        async def build() -> str | None:
            assert self._context_builder is not None
            return await self._context_builder(
                ContextInput(
                    zep=self._zep_client,
                    user_uuid=self._user_uuid,
                    thread_uuid=self._thread_uuid,
                    user_message=user_text,
                    session=_current_session(self),
                )
            )

        persist_result, context_result = await asyncio.gather(
            persist(),
            build(),
            return_exceptions=True,
        )
        if isinstance(persist_result, BaseException):
            logger.warning("Failed to persist the user turn in Zep: %s", persist_result)
        if isinstance(context_result, BaseException):
            logger.warning("The context builder failed; context injection was skipped")
            return None
        return context_result


class ZepGraphAgent(_ZepAgent):
    """LiveKit agent with Context Graph memory."""

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_uuid: str,
        user_name: str | None = None,
        max_characters: int = 4000,
        search_filters: SearchFilters | None = None,
        template_uuid: str | None = None,
        context_builder: GraphContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        **kwargs: Any,
    ) -> None:
        if not graph_uuid:
            raise AgentConfigurationError("graph_uuid must be a non-empty string")
        if "on_created" in kwargs:
            raise TypeError("ZepGraphAgent does not support 'on_created'")
        _render_context(context_template, "")
        super().__init__(**kwargs)
        self._zep_client = zep_client
        self._graph_uuid = graph_uuid
        self._user_name = user_name
        self._max_characters = max_characters
        self._search_filters = search_filters
        self._template_uuid = template_uuid
        self._context_builder = context_builder
        self._context_template = context_template

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        content = f"[Assistant]: {content_text}" if self._user_name else content_text
        await self._add_graph_message(content, "assistant")

    async def _add_graph_message(self, content: str, role: str) -> None:
        try:
            await self._zep_client.graph.episode.add(
                self._graph_uuid,
                data=truncate_graph_data(content, label=f"{role} graph data"),
                type="message",
            )
        except Exception:
            logger.warning("Failed to store the %s message in the graph", role, exc_info=True)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        await super().on_user_turn_completed(turn_ctx, new_message)
        user_text = (new_message.text_content or "").strip()
        if not user_text:
            return

        content = f"[{self._user_name}]: {user_text}" if self._user_name else user_text
        await self._add_graph_message(
            truncate_graph_data(content, label="user graph data"),
            "user",
        )
        query = user_text[:400]
        if self._context_builder is None:
            context = await self._retrieve_graph_context(query)
        else:
            context = await self._build_custom_context(query)
        if context:
            turn_ctx.add_message(
                role="system",
                content=_render_context(self._context_template, context),
            )

    async def _build_custom_context(self, query: str) -> str | None:
        assert self._context_builder is not None
        try:
            return await self._context_builder(
                GraphContextInput(
                    zep=self._zep_client,
                    graph_uuid=self._graph_uuid,
                    user_message=query,
                    session=_current_session(self),
                )
            )
        except Exception:
            logger.warning("The graph context builder failed", exc_info=True)
            return None

    async def _retrieve_graph_context(self, query: str) -> str | None:
        try:
            kwargs: dict[str, Any] = {
                "query": query,
                "filters": self._search_filters,
                "max_characters": self._max_characters,
            }
            if self._template_uuid is not None:
                kwargs["template_uuid"] = self._template_uuid
            response = await self._zep_client.graph.get_context(self._graph_uuid, **kwargs)
            return response.context if response else None
        except Exception:
            logger.warning("Failed to retrieve Context Graph context", exc_info=True)
            return None
