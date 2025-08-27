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
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .exceptions import AgentConfigurationError

logger = logging.getLogger(__name__)


class ZepMemoryAgent(agents.Agent):
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
        instructions: Instructions for the agent behavior
        **kwargs: All other LiveKit Agent parameters (chat_ctx, tools, stt, llm, tts, etc.)
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str,
        thread_id: str,
        context_mode: Literal["basic", "summary"] | None,
        **kwargs: Any
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
        
        logger.info(f"ZepMemoryAgent initialized for user {user_id}, thread {thread_id}")

    async def on_enter(self) -> None:
        """Called when the agent enters a conversation."""
        await super().on_enter()
        logger.info(f"Agent entered conversation for user {self._user_id}")
        
        # Hook into session events to capture assistant messages
        if hasattr(self, 'session'):
            self._setup_session_handlers()

    def _setup_session_handlers(self) -> None:
        """Set up event handlers on the session to capture assistant responses."""
        logger.debug("Setting up session event handlers for assistant message capture")
        
        @self.session.on("conversation_item_added")
        def on_conversation_item_added(event) -> None:
            """Handle conversation item addition events to capture assistant responses."""
            logger.debug(f"🗨️ Conversation item added: {type(event)}")
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._handle_conversation_item(event))

    async def _handle_conversation_item(self, event) -> None:
        """Handle conversation item from session event."""
        try:
            logger.debug(f"Processing conversation item event: {type(event)}")
            
            # Extract conversation item from event
            if not hasattr(event, 'item'):
                logger.debug("Event missing 'item' attribute")
                return
                
            item = event.item
            logger.debug(f"Found conversation item: {type(item)}")
            
            # Validate item has required message attributes
            if not (hasattr(item, 'role') and hasattr(item, 'content')):
                logger.debug("Item missing role/content attributes")
                return
                
            role = item.role
            content = item.content
            
            # Only store assistant messages (user messages handled in on_user_turn_completed)
            if role == "assistant":
                content_text = self._extract_text_content(content)
                if content_text.strip():
                    await self._store_assistant_message(content_text.strip(), item)
            else:
                logger.debug(f"Skipping {role} message in conversation item handler")
                
        except Exception as e:
            logger.error(f"Failed to handle conversation item: {e}")

    def _extract_text_content(self, content) -> str:
        """Extract text content from various LiveKit content formats."""
        if isinstance(content, str):
            return content
            
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if hasattr(item, 'text'):
                    text_parts.append(item.text)
                elif isinstance(item, str):
                    text_parts.append(item)
            return " ".join(text_parts)
            
        return str(content)

    async def _store_assistant_message(self, content_text: str, item) -> None:
        """Store assistant message in Zep thread memory."""
        try:
            logger.info(f"Storing assistant response in Zep: {content_text[:100]}...")
            
            zep_message = Message(
                content=content_text,
                role="assistant",
                name=getattr(item, 'name', None)
            )
            
            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id,
                messages=[zep_message]
            )
            
            logger.info("✅ Assistant response stored in Zep")
            
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
        if not user_text.strip():
            logger.debug("Empty user message, skipping")
            return
            
        logger.info(f"Processing user message: {user_text[:100]}...")

        try:
            logger.info(f"Adding user message to Zep thread {self._thread_id}")
            zep_message = Message(
                content=user_text.strip(),
                role="user"
            )
            
            await self._zep_client.thread.add_messages(
                thread_id=self._thread_id,
                messages=[zep_message]
            )
            logger.info("✅ User message stored in Zep")
            
        except Exception as e:
            logger.warning(f"Failed to store user message in Zep: {e}")

        try:
            logger.info("Retrieving relevant context from Zep")
            memory_result = await self._zep_client.thread.get_user_context(
                thread_id=self._thread_id, 
                mode=self._context_mode
            )
            
            if memory_result and memory_result.context:
                context = memory_result.context
                logger.info(f"Retrieved context: {context[:200]}...")
                
                turn_ctx.add_message(
                    role="system",
                    content=f"Relevant user context:\n{context}"
                )
                logger.info("✅ Context injected into conversation")
            else:
                logger.info("No relevant context found")
                
        except Exception as e:
            logger.warning(f"Failed to retrieve context from Zep: {e}")


    async def on_exit(self) -> None:
        """Called when the agent exits a conversation."""
        await super().on_exit()
        logger.info(f"Agent exiting for user {self._user_id}")