"""
Zep Memory integration for LiveKit agents.

This module provides the ZepMemoryAgent class that integrates Zep's memory capabilities
with LiveKit's voice AI agent framework
"""

import asyncio
import logging
from typing import Any, Literal

from livekit import agents
from livekit.agents.llm.chat_context import ChatContext, ChatMessage
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.graph.utils import compose_context_string
from zep_cloud.types import Message, Reranker

from .exceptions import AgentConfigurationError

logger = logging.getLogger(__name__)


class ZepUserAgent(agents.Agent):
    """
    LiveKit agent with Zep memory capabilities.

    A drop-in replacement for LiveKit's Agent that adds persistent memory:
    - Stores user and assistant messages in Zep threads
    - Retrieves relevant context and injects it for personalized responses
    - Accepts all standard LiveKit Agent parameters

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        user_id: User identifier for memory isolation and personalization
        thread_id: Thread identifier for conversation continuity
        user_message_name: Optional name to set on user messages in Zep
        assistant_message_name: Optional name to set on assistant messages in Zep
        **kwargs: All other LiveKit Agent parameters (chat_ctx, tools, stt, llm, tts, etc.)
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str,
        thread_id: str,
        context_mode: Literal["basic", "summary"] | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not user_id:
            raise AgentConfigurationError("user_id must be a non-empty string")
        if not thread_id:
            raise AgentConfigurationError("thread_id must be a non-empty string")

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._user_id = user_id
        self._thread_id = thread_id
        self._context_mode = context_mode or "basic"
        self._user_message_name = user_message_name
        self._assistant_message_name = assistant_message_name

    async def on_enter(self) -> None:
        """Called when the agent enters a conversation."""
        await super().on_enter()

        # Hook into session events to capture assistant messages
        if hasattr(self, "session"):
            self._setup_session_handlers()

    def _setup_session_handlers(self) -> None:
        """Set up event handlers on the session to capture assistant responses."""

        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event: Any) -> None:
            """Handle conversation item addition events to capture assistant responses."""
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event: Any) -> None:
        """Handle conversation item from session event."""
        try:
            # Extract conversation item from event
            if not hasattr(event, "item"):
                return

            item = event.item

            # Validate item has required message attributes
            if not (hasattr(item, "role") and hasattr(item, "content")):
                return

            role = item.role
            content = item.content

            # Only store assistant messages (user messages handled in on_user_turn_completed)
            if role == "assistant":
                content_text = self._extract_text_content(content)
                if content_text.strip():
                    await self._store_assistant_message(content_text.strip(), item)

        except Exception as e:
            logger.error(f"Failed to handle conversation item: {e}")

    def _extract_text_content(self, content: Any) -> str:
        """Extract text content from various LiveKit content formats."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif isinstance(item, str):
                    text_parts.append(item)
            return " ".join(text_parts)

        return str(content)

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        """Store assistant message in Zep thread memory."""
        try:
            # Use custom assistant name if provided, otherwise fallback to item name
            message_name = self._assistant_message_name or getattr(item, "name", None)
            
            zep_message = Message(
                content=content_text, role="assistant", name=message_name
            )

            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id, messages=[zep_message]
            )

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Store user message in Zep
        2. Retrieve relevant context from Zep
        3. Inject context into conversation
        """
        await super().on_user_turn_completed(turn_ctx, new_message)

        user_text = new_message.text_content
        if not user_text or not user_text.strip():
            return

        try:
            zep_message = Message(
                content=user_text.strip(), role="user", name=self._user_message_name
            )

            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id, messages=[zep_message]
            )

        except Exception as e:
            logger.warning(f"Failed to store user message in Zep: {e}")

        try:
            memory_result = await self._zep_client.thread.get_user_context(
                thread_id=self._thread_id, mode=self._context_mode
            )

            if memory_result and memory_result.context:
                context = memory_result.context

                turn_ctx.add_message(role="system", content=f"Relevant user context:\n{context}")

        except Exception as e:
            logger.warning(f"Failed to retrieve context from Zep: {e}")

    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()


class ZepGraphAgent(agents.Agent):
    """
    LiveKit agent with Zep graph memory capabilities.

    A drop-in replacement for LiveKit's Agent that adds persistent knowledge storage:
    - Stores user and assistant messages in Zep graph
    - Performs hybrid search to retrieve relevant context from edges, nodes, and episodes
    - Uses smart context composition for comprehensive knowledge retrieval
    - Optional user name prefixing for message attribution

    User Identification:
    - If user_name is provided, messages are stored as "[UserName]: message" and "[Assistant]: response"
    - If user_name is None, messages are stored without prefixes
    - Designed for per-user agent instances (typical deployment pattern)

    Args:
        zep_client: Initialized AsyncZep client for memory operations
        graph_id: Graph identifier for knowledge storage
        user_name: Optional user name for message prefixing (e.g., "Alice", "Bob")
        facts_limit: Maximum number of facts/edges to retrieve (default: 20)
        entity_limit: Maximum number of entities/nodes to retrieve (default: 5)
        episode_limit: Maximum number of episodes to retrieve (default: 3)
        search_filters: Optional filters for graph search
        reranker: Optional reranker for search results
        **kwargs: All other LiveKit Agent parameters
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_id: str,
        user_name: str | None = None,
        facts_limit: int = 15,
        entity_limit: int = 5,
        episode_limit: int = 2,
        search_filters: SearchFilters | None = None,
        reranker: Reranker | None = "rrf",
        **kwargs: Any,
    ) -> None:
        if not graph_id:
            raise AgentConfigurationError("graph_id must be a non-empty string")

        # Initialize base Agent with all parameters passed through
        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._graph_id = graph_id
        self._user_name = user_name
        self._facts_limit = facts_limit
        self._entity_limit = entity_limit
        self._episode_limit = episode_limit
        self._search_filters = search_filters
        self._reranker = reranker

    async def on_enter(self) -> None:
        """Called when the agent enters a conversation."""
        await super().on_enter()

        # Hook into session events to capture assistant messages
        if hasattr(self, "session"):
            self._setup_session_handlers()

    def _setup_session_handlers(self) -> None:
        """Set up event handlers on the session to capture assistant responses."""

        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event: Any) -> None:
            """Handle conversation item addition events to capture assistant responses."""
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event: Any) -> None:
        """Handle conversation item from session event."""
        try:
            # Extract conversation item from event
            if not hasattr(event, "item"):
                return

            item = event.item

            # Validate item has required message attributes
            if not (hasattr(item, "role") and hasattr(item, "content")):
                return

            role = item.role
            content = item.content

            # Only store assistant messages (user messages handled in on_user_turn_completed)
            if role == "assistant":
                content_text = self._extract_text_content(content)
                if content_text.strip():
                    await self._store_assistant_message(content_text.strip(), item)

        except Exception as e:
            logger.error(f"Failed to handle conversation item: {e}")

    def _extract_text_content(self, content: Any) -> str:
        """Extract text content from various LiveKit content formats."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif isinstance(item, str):
                    text_parts.append(item)
            return " ".join(text_parts)

        return str(content)

    async def _store_assistant_message(self, content_text: str, item: Any) -> None:
        """Store assistant message in Zep graph."""
        try:
            # Prefix assistant messages for consistency when user has a name
            if self._user_name:
                message_data = f"[Assistant]: {content_text}"
            else:
                message_data = content_text

            await self._zep_client.graph.add(
                graph_id=self._graph_id, type="message", data=message_data
            )

        except Exception as e:
            logger.warning(f"Failed to store assistant response: {e}")

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """
        Handle user turn completion - store message and inject memory context.

        1. Store user message in Zep graph
        2. Perform hybrid search to retrieve relevant context
        3. Inject context into conversation using smart composition
        """
        await super().on_user_turn_completed(turn_ctx, new_message)

        user_text = new_message.text_content
        if not user_text or not user_text.strip():
            return

        # Step 1: Store user message in Zep graph with user identification
        try:
            # Prefix message with user name if provided
            message_data = user_text.strip()
            if self._user_name:
                message_data = f"[{self._user_name}]: {message_data}"

            await self._zep_client.graph.add(
                graph_id=self._graph_id, type="message", data=message_data
            )

        except Exception as e:
            logger.warning(f"Failed to store user message in Zep graph: {e}")

        # Step 2: Retrieve relevant context using hybrid search
        try:
            context = await self._retrieve_graph_context(user_text[:400])  # Limit query length

            if context:
                # Step 3: Inject context as system message
                turn_ctx.add_message(
                    role="system", content=f"Relevant knowledge from memory:\n{context}"
                )

        except Exception as e:
            logger.warning(f"Failed to retrieve context from Zep graph: {e}")

    async def _retrieve_graph_context(self, query: str) -> str | None:
        """
        Retrieve and compose context from graph using hybrid search.

        - Search for edges (facts), nodes (entities) and episodes concurrently
        - Compose a context string using the graph utilities
        """
        try:
            # Perform parallel searches like in autogen
            search_functions = []

            if self._facts_limit:
                # Search for facts/relationships (edges)
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._facts_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="edges",
                    ),
                )

            if self._entity_limit:
                # Search for entities (nodes)
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._entity_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="nodes",
                    ),
                )

            if self._episode_limit:
                # Search for episodes
                search_functions.append(
                    self._zep_client.graph.search(
                        graph_id=self._graph_id,
                        query=query,
                        limit=self._episode_limit,
                        search_filters=self._search_filters,
                        reranker=self._reranker,
                        scope="episodes",
                    ),
                )

            results = await asyncio.gather(*search_functions)

            edges = []
            nodes = []
            episodes = []

            # Collect all results
            for result in results:
                if result.edges:
                    edges.extend(result.edges)
                if result.nodes:
                    nodes.extend(result.nodes)
                if result.episodes:
                    episodes.extend(result.episodes)

            if not edges and not nodes and not episodes:
                return None

            context = compose_context_string(edges, nodes, episodes)
            return context

        except Exception as e:
            logger.error(f"Error retrieving graph context: {e}")
            return None

    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()
