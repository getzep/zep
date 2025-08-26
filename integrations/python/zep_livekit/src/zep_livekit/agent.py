"""
Zep Memory integration for LiveKit agents.

This module provides the ZepMemoryAgent class that integrates Zep's memory retrieval
capabilities with LiveKit's voice AI agent framework.
"""

import asyncio
import logging
from typing import Any, TYPE_CHECKING
from typing_extensions import NotRequired, TypedDict, Unpack

from livekit import agents
from livekit.agents import llm, stt, tts, vad
from livekit.agents.types import NotGivenOr
from zep_cloud.client import AsyncZep

from .exceptions import AgentConfigurationError, MemoryRetrievalError

if TYPE_CHECKING:
    from livekit.agents.llm import mcp
    from livekit.agents.voice.agent_session import TurnDetectionMode


class AgentKwargs(TypedDict, total=False):
    """TypedDict for LiveKit Agent constructor parameters."""
    
    # Required parameter - must be present in kwargs
    instructions: str
    
    # Optional parameters with proper LiveKit types
    chat_ctx: NotRequired[NotGivenOr[llm.ChatContext | None]]
    tools: NotRequired[list[llm.FunctionTool | llm.RawFunctionTool] | None]
    turn_detection: NotRequired[NotGivenOr["TurnDetectionMode | None"]]
    stt: NotRequired[NotGivenOr[stt.STT | None]]
    vad: NotRequired[NotGivenOr[vad.VAD | None]]
    llm: NotRequired[NotGivenOr[llm.LLM | llm.RealtimeModel | None]]
    tts: NotRequired[NotGivenOr[tts.TTS | None]]
    mcp_servers: NotRequired[NotGivenOr[list["mcp.MCPServer"] | None]]
    allow_interruptions: NotRequired[NotGivenOr[bool]]
    min_consecutive_speech_delay: NotRequired[NotGivenOr[float]]
    use_tts_aligned_transcript: NotRequired[NotGivenOr[bool]]


class ZepMemoryAgent(agents.Agent):
    """
    LiveKit agent with Zep memory retrieval and context injection.
    
    This agent integrates with Zep's persistent memory system to provide context-aware
    conversations by retrieving and injecting relevant memories into agent prompts.
    
    ## Memory Responsibilities
    - **Context Retrieval**: Fetch conversation history and relevant memories from Zep
    - **Memory Injection**: Add memory context to agent prompts for personalized responses
    - **Read Operations**: Handle all Zep read operations (ZepAgentSession handles writes)
    
    ## Memory Flow
    1. User message arrives → Retrieve relevant context from Zep
    2. Inject memory context as system message → Agent generates informed response
    3. Memory-aware response considers conversation history and user knowledge
    
    Args:
        zep_client: Initialized AsyncZep client for memory operations
        user_id: User identifier for memory isolation and personalization
        thread_id: Thread identifier for conversation continuity
        **kwargs: All LiveKit Agent parameters (instructions, llm, stt, tts, etc.)
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str,
        thread_id: str,
        **kwargs: Unpack[AgentKwargs],
    ) -> None:
        """Initialize ZepMemoryAgent with memory retrieval capabilities."""
        # Validate required parameters
        if not isinstance(zep_client, AsyncZep):
            raise AgentConfigurationError("zep_client must be an instance of AsyncZep")
        if not user_id or not isinstance(user_id, str):  
            raise AgentConfigurationError("user_id must be a non-empty string")
        if not thread_id or not isinstance(thread_id, str):
            raise AgentConfigurationError("thread_id must be a non-empty string")

        super().__init__(**kwargs)

        self._zep_client = zep_client
        self._user_id = user_id
        self._thread_id = thread_id

        self._logger = logging.getLogger(__name__)
        self._logger.info(f"ZepMemoryAgent initialized for user {user_id}, thread {thread_id}")

    @property
    def user_id(self) -> str:
        """Get the user ID for this agent."""
        return self._user_id

    @property
    def thread_id(self) -> str:
        """Get the thread ID for this agent."""
        return self._thread_id

    async def on_enter(self) -> None:
        """
        Called when the agent enters a conversation.
        """
        await super().on_enter()
        self._logger.info(f"Agent entered conversation for user {self._user_id}, thread {self._thread_id}")

    async def on_user_turn_completed(self, chat_ctx: agents.ChatContext, **kwargs: Any) -> None:
        """
        Handle user turn completion and inject memory context.
        
        Called after each user turn completes. Retrieves relevant memory context
        from Zep and injects it into the conversation for personalized responses.
        
        Note: Message storage is handled by ZepAgentSession.

        Args:
            chat_ctx: Current chat context for memory injection
            **kwargs: Additional parameters including 'new_message' from LiveKit
        """
        await super().on_user_turn_completed(chat_ctx, **kwargs)
        
        try:
            self._logger.info("🔄 Processing user turn for memory injection")
            
            # Extract user message from LiveKit's new_message parameter
            new_message = kwargs.get('new_message')
            if not new_message:
                self._logger.debug("No new_message in kwargs, skipping memory injection")
                return
            self._logger.info(f"new_message {new_message}")
            # Only process user messages (assistant messages don't trigger memory retrieval)
            if not (hasattr(new_message, 'role') and new_message.role == 'user'):
                self._logger.debug(f"Skipping non-user message: {getattr(new_message, 'role', 'unknown')}")
                return
                
            # Extract text content for memory search
            user_text = self._extract_message_text(new_message)
            if not user_text.strip():
                self._logger.debug("Empty user message, skipping memory injection")
                return
                
            self._logger.info(f"🔍 Injecting memory for user message: {user_text[:50]}...")
            await self._inject_memory_context(chat_ctx, user_text)
            
        except Exception as e:
            self._logger.error(f"Memory injection failed: {e}")
            # Don't raise to avoid breaking conversation flow
            
    def _extract_message_text(self, message) -> str:
        """
        Extract text content from LiveKit message formats.
        
        Args:
            message: LiveKit message object with content attribute
            
        Returns:
            Extracted text content as string
        """
        if not hasattr(message, 'content'):
            return ""
            
        content = message.content
        
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

    async def _inject_memory_context(self, chat_ctx: agents.ChatContext, user_text: str) -> None:
        """
        Inject memory context into chat conversation.
        
        Retrieves relevant memories from Zep and injects them as system messages
        to provide context-aware responses.
        
        Args:
            chat_ctx: LiveKit chat context for message injection
            user_text: User message text for contextual memory search
        """
        try:
            self._logger.info(f"🧠 Retrieving memory context for user message: {user_text[:50]}...")
            
            memory_parts = []

            # Retrieve thread context and recent conversation history
            try:
                self._logger.debug(f"Requesting memory from thread: {self._thread_id}")
                memory_result = await asyncio.wait_for(
                    self._zep_client.thread.get_user_context(thread_id=self._thread_id, fast=True),
                    timeout=5.0
                )
                
                self._logger.info("✅ Retrieved thread context from Zep")
                
                # Add thread context if available
                if memory_result.context:
                    memory_parts.append(f"Context: {memory_result.context}")
                    self._logger.debug("Added thread context to memory")
                
                # Add recent conversation history
                if memory_result.messages:
                    recent_messages = memory_result.messages[-5:]  # Last 5 messages
                    if recent_messages:
                        history_lines = [
                            f"{msg.role}: {msg.content}" for msg in recent_messages
                        ]
                        memory_parts.append("Recent history:\n" + "\n".join(history_lines))
                        self._logger.debug(f"Added {len(recent_messages)} recent messages")
                else:
                    self._logger.debug("No messages found in thread context")
                self._logger.info(f"memory_parts {memory_parts}")
            except asyncio.TimeoutError:
                self._logger.warning(f"Timeout retrieving memory from thread {self._thread_id}")
            except Exception as e:
                self._logger.warning(f"Failed to retrieve thread context from {self._thread_id}: {e}")
                # Add more detail if it's a 404 or similar
                if hasattr(e, 'status_code'):
                    self._logger.debug(f"HTTP status: {e.status_code}")
                if hasattr(e, 'body'):
                    self._logger.debug(f"Response body: {e.body}")

            # Inject memory context if available
            if memory_parts:
                memory_context = "\n\n".join(memory_parts)
                self._logger.info(f"💾 Injecting memory context: {memory_context[:100]}...")
                
                # Add memory context as system message using correct LiveKit API
                try:
                    memory_msg = chat_ctx.add_message(
                        role="system",
                        content=f"Memory context for this conversation:\n\n{memory_context}"
                    )
                    self._logger.info(f"✅ Injected memory context with {len(memory_parts)} parts")
                    self._logger.debug(f"Memory message ID: {getattr(memory_msg, 'id', 'unknown')}")
                    
                except Exception as inject_error:
                    self._logger.error(f"Failed to inject memory context: {inject_error}")
            else:
                self._logger.debug("No memory context available to inject")

        except Exception as e:
            self._logger.error(f"Memory injection error: {e}")
            # Don't raise to avoid breaking conversation flow


    async def update_chat_ctx(self, chat_ctx: agents.ChatContext, **kwargs: Any) -> None:
        """
        Update chat context with Zep memory before agent responses.
        
        Called by LiveKit right before LLM generation. This is an alternative
        injection point if on_user_turn_completed doesn't fire reliably.
        
        Args:
            chat_ctx: Chat context to update with memory
            **kwargs: Additional parameters
        """
        await super().update_chat_ctx(chat_ctx, **kwargs)
        
        # Note: Memory injection is primarily handled in on_user_turn_completed
        # This method is kept as backup for edge cases
        self._logger.debug("update_chat_ctx called - memory injection handled elsewhere")


    async def on_exit(self) -> None:
        """
        Called when the agent exits a conversation.
        
        Performs any cleanup needed for memory storage.
        """
        await super().on_exit()
        self._logger.info(f"Agent exiting for user {self._user_id}, thread {self._thread_id}")

    async def clear_memory(self) -> None:
        """
        Clear all memory for this thread.
        
        This will delete the entire thread and all its messages.
        Note: This operation cannot be undone.
        
        Raises:
            MemoryStorageError: If memory clearing fails
        """
        try:
            await self._zep_client.thread.delete(thread_id=self._thread_id)
            self._logger.info(f"Cleared memory for thread {self._thread_id}")
        except Exception as e:
            self._logger.error(f"Error clearing memory: {e}")
            raise MemoryStorageError(f"Failed to clear memory: {e}")

