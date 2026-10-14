"""Zep context provider for Microsoft Agent Framework."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_framework import ContextProvider
from zep_cloud import Message as ZepMessage
from zep_cloud.client import AsyncZep

from ._text import truncate_message_content
from .search import create_zep_search_tool

if TYPE_CHECKING:
    from agent_framework import AgentSession, Message, SessionContext, SupportsAgentRun

    from .search import ZepSearchTool

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_ID = "zep"
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
    """Inputs for a custom context builder."""

    zep: AsyncZep
    user_uuid: str
    thread_uuid: str
    graph_uuid: str | None
    user_message: str
    session_context: SessionContext | None = None


ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]


class ZepContextProvider(ContextProvider):
    """Persist agent turns and inject Zep context into model instructions.

    Create the Zep user and thread before constructing the provider. The
    provider uses the server-generated UUIDs and never provisions resources on
    the agent turn path.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str,
        thread_uuid: str,
        graph_uuid: str | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str | None = "Assistant",
        source_id: str = DEFAULT_SOURCE_ID,
        ignore_roles: Sequence[str] | None = None,
        context_builder: ContextBuilder | None = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        expose_search_tool: bool = False,
        search_pinned_params: dict[str, Any] | None = None,
        search_hidden_params: set[str] | None = None,
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
    ) -> None:
        super().__init__(source_id=source_id)
        if not user_uuid:
            raise ValueError("user_uuid must not be empty")
        if not thread_uuid:
            raise ValueError("thread_uuid must not be empty")
        if "{context}" not in context_template:
            raise ValueError("context_template must contain the literal '{context}' placeholder")
        if expose_search_tool and not graph_uuid:
            raise ValueError("graph_uuid is required when expose_search_tool is enabled")

        self._zep = zep_client
        self._user_uuid = user_uuid
        self._thread_uuid = thread_uuid
        self._graph_uuid = graph_uuid
        self._user_message_name = user_message_name
        self._assistant_message_name = assistant_message_name
        self._ignore_roles = list(ignore_roles) if ignore_roles is not None else None
        self._context_builder = context_builder
        self._context_template = context_template
        self._search_tool: ZepSearchTool | None = (
            create_zep_search_tool(
                zep_client=zep_client,
                graph_uuid=graph_uuid,
                search_pinned_params=search_pinned_params,
                search_hidden_params=search_hidden_params,
                search_filters=search_filters,
                bfs_origin_node_uuids=bfs_origin_node_uuids,
            )
            if expose_search_tool and graph_uuid is not None
            else None
        )
        self._user_turn_persisted = False

    @property
    def user_uuid(self) -> str:
        return self._user_uuid

    @property
    def thread_uuid(self) -> str:
        return self._thread_uuid

    @property
    def graph_uuid(self) -> str | None:
        return self._graph_uuid

    @staticmethod
    def _latest_user_text(messages: list[Message]) -> str | None:
        for message in reversed(messages):
            if message.role == "user" and message.text:
                return message.text
        for message in reversed(messages):
            if message.text:
                return message.text
        return None

    @staticmethod
    def _assistant_text(messages: list[Message] | None) -> str | None:
        if not messages:
            return None
        parts = [
            message.text for message in messages if message.role == "assistant" and message.text
        ]
        return "\n".join(parts) if parts else None

    def _format_context(self, context: str) -> str:
        return self._context_template.replace("{context}", context)

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        self._user_turn_persisted = False
        if self._search_tool is not None:
            context.extend_tools(self.source_id, [self._search_tool])

        user_text = self._latest_user_text(context.input_messages)
        if not user_text:
            return
        user_text = truncate_message_content(user_text, label="user message")

        if self._context_builder is None:
            persist_ok, context_block = await self._persist_with_return_context(user_text)
        else:
            persist_ok, context_block = await self._persist_and_build_context(user_text, context)
        self._user_turn_persisted = persist_ok

        if context_block:
            context.extend_instructions(self.source_id, self._format_context(context_block))

    async def _persist_with_return_context(self, user_text: str) -> tuple[bool, str | None]:
        try:
            response = await self._zep.thread.add_messages(
                thread_uuid=self._thread_uuid,
                messages=[ZepMessage(role="user", content=user_text, name=self._user_message_name)],
                return_context=True,
                ignore_roles=self._ignore_roles,
            )
            context_block = response.context if response else None
            return True, context_block
        except Exception:
            logger.warning(
                "Failed to persist the user message or retrieve context from Zep", exc_info=True
            )
            return False, None

    async def _persist_and_build_context(
        self,
        user_text: str,
        session_context: SessionContext,
    ) -> tuple[bool, str | None]:
        context_builder = self._context_builder
        assert context_builder is not None

        async def persist() -> None:
            await self._zep.thread.add_messages(
                thread_uuid=self._thread_uuid,
                messages=[ZepMessage(role="user", content=user_text, name=self._user_message_name)],
                ignore_roles=self._ignore_roles,
            )

        async def build() -> str | None:
            return await context_builder(
                ContextInput(
                    zep=self._zep,
                    user_uuid=self._user_uuid,
                    thread_uuid=self._thread_uuid,
                    graph_uuid=self._graph_uuid,
                    user_message=user_text,
                    session_context=session_context,
                )
            )

        persist_result, build_result = await asyncio.gather(
            persist(), build(), return_exceptions=True
        )
        if isinstance(build_result, BaseException):
            logger.warning(
                "The context builder failed. Context will not be injected.", exc_info=build_result
            )
            context_block = None
        else:
            context_block = build_result
        if isinstance(persist_result, BaseException):
            logger.warning("Failed to persist the user message to Zep", exc_info=persist_result)
            return False, context_block
        return True, context_block

    async def after_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        if not self._user_turn_persisted:
            return
        assistant_text = self._assistant_text(
            context.response.messages if context.response else None
        )
        if not assistant_text:
            return
        assistant_text = truncate_message_content(assistant_text, label="assistant message")
        try:
            await self._zep.thread.add_messages(
                thread_uuid=self._thread_uuid,
                messages=[
                    ZepMessage(
                        role="assistant",
                        content=assistant_text,
                        name=self._assistant_message_name,
                    )
                ],
                ignore_roles=self._ignore_roles,
            )
        except Exception:
            logger.warning("Failed to persist the assistant response to Zep", exc_info=True)
