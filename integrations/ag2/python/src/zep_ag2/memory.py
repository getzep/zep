"""
Zep Memory Manager for AG2.

This module provides the ZepMemoryManager class that integrates Zep with AG2 agents
via system message injection and message storage.

Unlike Microsoft AutoGen v4 which has a formal Memory base class, AG2 uses
composition-based patterns. ZepMemoryManager enriches agents by:
- Injecting relevant memory context into system messages
- Storing conversation messages in Zep threads
- Retrieving session facts and context
"""

import logging
from typing import Any

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from zep_ag2.exceptions import ZepAG2ConfigError, ZepAG2MemoryError
from zep_ag2.tools import MESSAGE_MAX_CHARS, _run_sync, _truncate, _validate_role

logger = logging.getLogger(__name__)


class ZepMemoryManager:
    """
    Manages Zep memory for AG2 agents via system message injection.

    This class provides methods to enrich AG2 agents with memory context
    from Zep, store conversation messages, and retrieve session facts.

    Example:
        >>> from zep_cloud.client import AsyncZep
        >>> from zep_ag2 import ZepMemoryManager
        >>> zep = AsyncZep(api_key="your-key")
        >>> manager = ZepMemoryManager(zep, user_id="user123", session_id="sess456")
        >>> await manager.enrich_system_message(agent, query="project discussion")
    """

    def __init__(
        self,
        client: AsyncZep,
        user_id: str,
        session_id: str | None = None,
    ) -> None:
        """
        Initialize ZepMemoryManager.

        Args:
            client: An initialized AsyncZep instance.
            user_id: User ID for memory isolation (required).
            session_id: Optional thread/session ID for conversation-scoped memory.

        Raises:
            ZepAG2ConfigError: If client is not an AsyncZep instance or user_id is empty.
        """
        if not isinstance(client, AsyncZep):
            raise ZepAG2ConfigError("client must be an instance of AsyncZep")
        if not user_id:
            raise ZepAG2ConfigError("user_id is required")

        self._client = client
        self._user_id = user_id
        self._session_id = session_id

    @property
    def client(self) -> AsyncZep:
        """The underlying AsyncZep client."""
        return self._client

    @property
    def user_id(self) -> str:
        """The user ID for memory isolation."""
        return self._user_id

    @property
    def session_id(self) -> str | None:
        """The thread/session ID, if set."""
        return self._session_id

    async def get_memory_context(self, query: str | None = None, limit: int = 5) -> str:
        """
        Retrieve relevant memories as a formatted context string.

        If a query is provided, performs semantic search on the user's knowledge graph.
        If a session_id is set, also retrieves thread context.

        Args:
            query: Optional search query for semantic memory retrieval.
            limit: Maximum number of results to return.

        Returns:
            A formatted string containing relevant memory context, or empty string
            if no relevant memories are found.
        """
        parts: list[str] = []

        # Get thread context if session_id is set. get_user_context returns a
        # prompt-ready Context Block assembled from the whole user graph, so a
        # separate recent-messages read is redundant.
        if self._session_id:
            try:
                context_result = await self._client.thread.get_user_context(
                    thread_id=self._session_id,
                )
                if context_result.context:
                    parts.append(f"Memory context: {context_result.context}")
            except Exception as e:
                logger.error("Zep get_user_context failed: %s", type(e).__name__)

        # Search knowledge graph if query is provided
        if query:
            try:
                graph_results = await self._client.graph.search(
                    user_id=self._user_id,
                    query=query,
                    limit=limit,
                )
                facts: list[str] = []
                if graph_results.edges:
                    for edge in graph_results.edges:
                        facts.append(f"- {edge.fact}")
                if graph_results.nodes:
                    for node in graph_results.nodes:
                        summary = node.summary or "No summary"
                        facts.append(f"- {node.name}: {summary}")

                if facts:
                    parts.append("Relevant knowledge:\n" + "\n".join(facts))
            except Exception as e:
                logger.error("Zep graph.search failed: %s", type(e).__name__)

        return "\n\n".join(parts)

    async def enrich_system_message(
        self,
        agent: Any,
        query: str | None = None,
        limit: int = 5,
    ) -> None:
        """
        Inject memory context into an AG2 agent's system message.

        Retrieves relevant memories from Zep and appends them to the agent's
        existing system_message using agent.update_system_message().

        Args:
            agent: An AG2 ConversableAgent (or subclass) with system_message
                   and update_system_message() attributes.
            query: Optional search query for semantic retrieval.
            limit: Maximum number of memory results.
        """
        context = await self.get_memory_context(query, limit)
        if context:
            original_msg = agent.system_message
            agent.update_system_message(f"{original_msg}\n\n## Relevant Memory Context\n{context}")

    async def add_messages(self, messages: list[dict[str, str]]) -> None:
        """
        Store messages in Zep for future retrieval.

        Args:
            messages: A list of message dicts, each with 'content', 'role',
                      and optionally 'name' keys.

        Raises:
            ZepAG2ConfigError: If no session_id is set.
            ZepAG2MemoryError: If the Zep API call fails.
        """
        if not self._session_id:
            raise ZepAG2ConfigError(
                "session_id is required to add messages. "
                "Set session_id when creating ZepMemoryManager."
            )

        try:
            zep_messages = [
                Message(
                    content=_truncate(msg["content"], MESSAGE_MAX_CHARS, "message content"),
                    role=_validate_role(msg.get("role", "user")),
                    name=msg.get("name"),
                )
                for msg in messages
            ]
            await self._client.thread.add_messages(
                thread_id=self._session_id,
                messages=zep_messages,
            )
        except Exception as e:
            logger.error("Zep thread.add_messages failed: %s", type(e).__name__)
            raise ZepAG2MemoryError("Failed to add messages") from e

    async def get_session_facts(self) -> list[str]:
        """
        Get extracted facts from the current session.

        Returns:
            A list of fact strings extracted from the session.

        Raises:
            ZepAG2ConfigError: If no session_id is set.
        """
        if not self._session_id:
            raise ZepAG2ConfigError("session_id is required to get session facts.")

        try:
            context_result = await self._client.thread.get_user_context(
                thread_id=self._session_id,
            )
            if context_result.context:
                return [context_result.context]
            return []
        except Exception as e:
            logger.error("Zep get_user_context failed: %s", type(e).__name__)
            return []

    # Sync wrappers for non-async AG2 usage

    def get_memory_context_sync(self, query: str | None = None, limit: int = 5) -> str:
        """Synchronous wrapper for get_memory_context().

        Bridges to async via the package's shared background event loop, so it
        works on Python 3.11–3.13 and whether or not a loop is already running.
        """
        return str(_run_sync(self.get_memory_context(query, limit)))

    def enrich_system_message_sync(
        self,
        agent: Any,
        query: str | None = None,
        limit: int = 5,
    ) -> None:
        """Synchronous wrapper for enrich_system_message().

        Bridges to async via the package's shared background event loop.
        """
        _run_sync(self.enrich_system_message(agent, query, limit))
