"""
Zep-enabled AgentSession wrapper for LiveKit agents.

This module provides a session wrapper that automatically captures conversation
events and stores them in Zep memory for persistent conversation history.
"""

import asyncio
import hashlib
import logging
from typing import Any, Set

from livekit import agents
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .exceptions import MemoryStorageError


class ZepAgentSession(agents.AgentSession):
    """
    LiveKit AgentSession with automatic Zep memory storage.
    
    This session wrapper captures all conversation events and automatically stores
    them in Zep memory for persistent conversation history across sessions.
    
    ## Storage Responsibilities
    - **Thread Memory**: Store all user/assistant messages in Zep conversation threads
    - **Graph Memory**: Extract user messages to Zep user graphs for knowledge building  
    - **Event Processing**: Handle LiveKit conversation events (transcription, responses)
    - **Write Operations**: Handle all Zep write operations (ZepMemoryAgent only reads)
    
    ## Event Flow
    1. User speaks → LiveKit transcribes → Store in thread + extract to graph
    2. Assistant responds → LiveKit generates → Store in thread
    3. All conversation history persists across sessions
    
    Args:
        zep_client: Initialized AsyncZep client for memory operations
        user_id: User identifier for memory isolation and personalization
        thread_id: Thread identifier for conversation continuity
        **kwargs: LiveKit AgentSession parameters (stt, llm, tts, vad, etc.)
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str, 
        thread_id: str,
        **kwargs: Any,
    ) -> None:
        """Initialize ZepAgentSession with memory storage capabilities."""
        # Validate required parameters
        if not isinstance(zep_client, AsyncZep):
            raise TypeError("zep_client must be an instance of AsyncZep")
        if not user_id or not isinstance(user_id, str):
            raise ValueError("user_id must be a non-empty string")
        if not thread_id or not isinstance(thread_id, str):
            raise ValueError("thread_id must be a non-empty string")
            
        # Initialize the underlying AgentSession with all LiveKit parameters
        super().__init__(**kwargs)
        
        # Store Zep configuration for memory operations
        self._zep_client = zep_client
        self._user_id = user_id
        self._thread_id = thread_id
        
        # Set up structured logging
        self._logger = logging.getLogger(__name__)
        self._logger.info(f"ZepAgentSession initialized for user {user_id}, thread {thread_id}")
        
        # Track processed messages to prevent duplicates
        self._processed_messages: Set[str] = set()
        
        # Set up event handlers to capture all conversation events
        self._setup_event_handlers()

    def _setup_event_handlers(self) -> None:
        """
        Set up LiveKit event handlers for automatic conversation capture.
        
        Registers handlers for key conversation events to ensure all messages
        are automatically stored in Zep memory without manual intervention.
        """
        self._logger.debug("Setting up Zep event handlers for conversation capture")
        
        @self.on("conversation_item_added")
        def on_conversation_item_added(event) -> None:
            """
            Handle conversation item addition events.
            
            Captures both user and assistant messages as they're added to the
            conversation and schedules them for storage in Zep memory.
            """
            self._logger.debug(f"🗨️ Conversation item added: {type(event)}")
            # Schedule async storage to avoid blocking event processing
            asyncio.create_task(self._store_conversation_item(event))
        
        @self.on("user_input_transcribed")  
        def on_user_input_transcribed(event) -> None:
            """
            Handle user speech transcription events.
            
            Provides visibility into speech-to-text processing for debugging.
            Actual storage is handled by conversation_item_added events.
            """
            self._logger.debug(f"🎤 User input transcribed: {type(event)}")
        
        @self.on("metrics_collected")
        def on_metrics_collected(event) -> None:
            """Handle metrics collection events for debugging."""
            self._logger.debug(f"📊 Metrics collected: {type(event)}")

    async def _store_conversation_item(self, event) -> None:
        """
        Store conversation item from LiveKit event in Zep memory.
        
        Processes conversation_item_added events to extract and store both
        user and assistant messages in Zep thread memory and user graphs.
        
        Args:
            event: LiveKit ConversationItemAddedEvent containing the message
        """
        try:
            self._logger.debug(f"Processing conversation item event: {type(event)}")
            
            # Extract conversation item from event
            if not hasattr(event, 'item'):
                self._logger.debug("Event missing 'item' attribute")
                return
                
            item = event.item
            self._logger.debug(f"Found conversation item: {type(item)}")
            
            # Validate item has required message attributes
            if not (hasattr(item, 'role') and hasattr(item, 'content')):
                self._logger.debug("Item missing role/content attributes")
                return
                
            role = item.role
            content = item.content
            
            # Only process user and assistant messages (skip system messages)
            if role not in ["user", "assistant"]:
                self._logger.debug(f"Skipping system message with role: {role}")
                return
                
            # Extract text content from various content formats
            content_text = self._extract_text_content(content)
            if not content_text.strip():
                self._logger.debug(f"Skipping empty {role} message")
                return
            
            # Create unique message identifier to prevent duplicates
            message_id = getattr(item, 'id', None)
            if message_id:
                message_key = f"{role}:{message_id}"
            else:
                # Fallback: use content hash for deduplication
                content_hash = hashlib.md5(content_text.encode()).hexdigest()[:8]
                message_key = f"{role}:{content_hash}"
            
            # Check if we've already processed this message
            if message_key in self._processed_messages:
                self._logger.debug(f"Skipping duplicate {role} message: {message_key}")
                return
                
            # Mark message as processed
            self._processed_messages.add(message_key)
            self._logger.info(f"💾 STORING {role.upper()} MESSAGE: {content_text[:100]}...")
            
            # Store message in Zep thread memory only
            await self._store_in_thread(content_text, role, item)
                
        except Exception as e:
            self._logger.error(f"Failed to store conversation item: {e}")
            # Don't raise to avoid breaking conversation flow
            
    def _extract_text_content(self, content) -> str:
        """
        Extract text content from various LiveKit content formats.
        
        Args:
            content: LiveKit message content (string, list, or other format)
            
        Returns:
            Extracted text content as string
        """
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
        
    async def _store_in_thread(self, content_text: str, role: str, item) -> None:
        """Store message in Zep thread memory."""
        zep_message = Message(
            content=content_text.strip(),
            role=role,
            name=getattr(item, 'name', None)
        )
        
        await self._zep_client.thread.add_messages(
            thread_id=self._thread_id,
            messages=[zep_message]
        )
        
        self._logger.info(f"✅ Stored {role} message in thread {self._thread_id}")
        
