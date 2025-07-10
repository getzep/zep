"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.
"""

from typing import Any, Optional, Sequence
from autogen_core.memory import Memory, MemoryContent, MemoryMimeType, MemoryQueryResult, UpdateContextResult
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage


from zep_cloud.client import AsyncZep
from zep_cloud.types import Message




class ZepMemory(Memory):
    """
    A memory implementation that integrates with Zep for persistent storage
    and retrieval of conversation context and agent memories.
    """
    
    def __init__(
        self,
        client: AsyncZep,
        session_id: str,
        user_id: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        """
        Initialize ZepMemory with an AsyncZep client instance.
        
        Args:
            client: An initialized AsyncZep instance
            session_id: Session ID for memory isolation (required)
            user_id: Optional user ID for user-level memory
            **kwargs: Additional configuration options
        """
        if AsyncZep is None:
            raise ImportError(
                "zep_cloud package is required. Install it with: pip install zep-cloud"
            )
        
        if not isinstance(client, AsyncZep):
            raise TypeError("client must be an instance of AsyncZep")
            
        if not session_id:
            raise ValueError("session_id is required")
            
        self._client = client
        self._session_id = session_id
        self._user_id = user_id
        self._config = kwargs
    
    async def add(self, entry: MemoryContent) -> None:
        """
        Add a memory entry to Zep storage.
        
        If role_type is present in metadata, stores as a message in the session.
        If role_type is not present, stores as data in the user's graph.
        
        Args:
            entry: The memory content to store
            
        Raises:
            ImportError: If zep_cloud.types.Message is not available
            ValueError: If the memory content mime type is not supported
        """
        # Validate mime type - only support TEXT, MARKDOWN, and JSON
        supported_mime_types = {
            MemoryMimeType.TEXT,
            MemoryMimeType.MARKDOWN,
            MemoryMimeType.JSON
        }
        
        if entry.mime_type not in supported_mime_types:
            raise ValueError(
                f"Unsupported mime type: {entry.mime_type}. "
                f"ZepMemory only supports: {', '.join(str(mt) for mt in supported_mime_types)}"
            )
        
        # Extract user_id from metadata using pop (removes it from metadata)
        metadata_copy = entry.metadata.copy() if entry.metadata else {}
        message_user_id = metadata_copy.pop("user_id", None)
        
        if message_user_id:
            # Store as message in session (we have user_id)
            if Message is None:
                raise ImportError("zep_cloud.types.Message is not available")
            
            message = Message(
                role=message_user_id,  # Use user_id as role
                content=entry.content,
                role_type="user"  # Always "user" when user_id is present
            )
            
            # Add message to Zep session
            await self._client.memory.add(
                session_id=self._session_id,
                messages=[message]
            )
        else:
            # Store as data in the graph (no user_id in metadata)
            if not self._user_id:
                raise ValueError("user_id is required when storing graph data (no user_id in metadata)")
            
            # Determine data type based on mime type
            if entry.mime_type == MemoryMimeType.JSON:
                data_type = "json"
            else:
                data_type = "text"  # Both TEXT and MARKDOWN are stored as text
            
            # Add data to user's graph
            await self._client.graph.add(
                user_id=self._user_id,
                type=data_type,
                data=entry.content
            )
    
    async def query(
        self,
        query: str,
        limit: Optional[int] = None,
        **kwargs: Any
    ) -> Sequence[MemoryContent]:
        """
        Query memories from Zep storage using memory.get and graph.search.
        
        Args:
            query: Search query string
            limit: Maximum number of results to return
            **kwargs: Additional query parameters
            
        Returns:
            Sequence of matching memory content
        """
        results = []
        
        try:
            # Get memory context from session
            memory_result = await self._client.memory.get(session_id=self._session_id)
            
            if memory_result.context:
                results.append(MemoryContent(
                    content=memory_result.context,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={"source": "session_context"}
                ))
            
            # Get recent messages
            if memory_result.messages:
                for msg in memory_result.messages[:limit] if limit else memory_result.messages:
                    results.append(MemoryContent(
                        content=msg.content,
                        mime_type=MemoryMimeType.TEXT,
                        metadata={
                            "role": msg.role,
                            "role_type": msg.role_type,
                            "source": "session_messages"
                        }
                    ))
            
            # If we have a user_id, also search the user's graph
            if self._user_id:
                graph_results = await self._client.graph.search(
                    user_id=self._user_id,
                    query=query,
                    limit=limit or 5,
                    **kwargs
                )
                
                # Add graph search results
                for edge in graph_results.edges:
                    results.append(MemoryContent(
                        content=edge.fact,
                        mime_type=MemoryMimeType.TEXT,
                        metadata={
                            "source": "user_graph",
                            "score": getattr(edge, 'score', None)
                        }
                    ))
                    
        except Exception as e:
            # Log error but don't fail completely
            print(f"Error querying Zep memory: {e}")
        
        return results
    
    async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
        """
        Update the agent's model context with retrieved memories.
        
        Gets memory from Zep, and if memory exists, includes up to 10 last messages
        from history and adds the memory context as a system message.
        
        Args:
            model_context: The model context to update
            
        Returns:
            UpdateContextResult with the memories that were retrieved
        """
        try:
            # Get messages from current context
            messages = await model_context.get_messages()
            if not messages:
                return UpdateContextResult(memories=MemoryQueryResult(results=[]))

            # Get memory from Zep session
            memory_result = await self._client.memory.get(session_id=self._session_id)
            
            memory_contents = []
            memory_parts = []
            
            # If we have memory context, include it
            if memory_result.context:
                memory_contents.append(MemoryContent(
                    content=memory_result.context,
                    mime_type=MemoryMimeType.TEXT,
                    metadata={"source": "session_context"}
                ))
                memory_parts.append(f"Memory context: {memory_result.context}")
                
                # Only include recent messages if we have memory
                if memory_result.messages:
                    recent_messages = memory_result.messages[-10:]  # Get last 10 messages
                    if recent_messages:
                        message_history = []
                        for msg in recent_messages:
                            message_history.append(f"{msg.role}: {msg.content}")
                        memory_parts.append(f"Recent conversation:\n" + "\n".join(message_history))
            
            # If we have memory parts, add them to the context as a system message
            if memory_parts:
                memory_context = "\n\n".join(memory_parts)
                await model_context.add_message(SystemMessage(content=memory_context))
            
            return UpdateContextResult(memories=MemoryQueryResult(results=memory_contents))
            
        except Exception as e:
            # Log error but don't fail completely
            print(f"Error updating context with Zep memory: {e}")
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))
    
    async def clear(self) -> None:
        """
        Clear all memories from Zep storage by deleting the session.
        
        This will delete the entire session and all its messages.
        Note: This operation cannot be undone.
        """
        try:
            # Delete the session - this clears all messages and memory for this session
            await self._client.memory.delete_session(session_id=self._session_id)
            
        except Exception as e:
            print(f"Error clearing Zep memory: {e}")
            raise
    
    async def close(self) -> None:
        """
        Clean up Zep client resources.
        
        Note: This method does not close the AsyncZep instance since it was
        provided externally. The caller is responsible for managing the client lifecycle.
        """
        # The client was provided externally, so we don't close it here
        # The caller is responsible for closing the client when appropriate
        pass


__all__ = ["ZepMemory"]