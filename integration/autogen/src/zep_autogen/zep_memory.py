"""
Zep Memory integration for AutoGen.

This module provides memory classes that integrate Zep with AutoGen's memory system.
"""

from typing import Any, Optional, Sequence
from autogen_core.memory import Memory, MemoryContent, MemoryMimeType


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
        Add a memory entry to Zep storage as a message.
        
        Args:
            entry: The memory content to store
            
        Raises:
            ImportError: If zep_cloud.types.Message is not available
            ValueError: If the memory content mime type is not supported
        """
        if Message is None:
            raise ImportError("zep_cloud.types.Message is not available")
        
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
            
        # Convert MemoryContent to Zep Message
        message = Message(
            role="system" if entry.metadata and entry.metadata.get("role") == "system" else "user",
            content=entry.content,
            role_type="assistant" if entry.metadata and entry.metadata.get("role_type") == "assistant" else "user"
        )
        
        # Add message to Zep session
        await self._client.memory.add(
            session_id=self._session_id,
            messages=[message]
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
    
    async def update_context(self, context: Any) -> None:
        """
        Update the agent's model context with retrieved memories.
        
        Args:
            context: The model context to update
        """
        # This is typically handled by AutoGen automatically
        # when the memory is used in an agent
        pass
    
    async def clear(self) -> None:
        """Clear all memories from Zep storage."""
        # Note: Zep doesn't provide a direct way to clear session messages
        # This would typically require deleting and recreating the session
        # For now, we'll leave this as a no-op since session deletion
        # might affect other parts of the system
        pass
    
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