"""
Memory management utilities for Zep LiveKit integration.

This module provides utility functions for memory operations and configuration.
"""

import logging
from typing import Dict, List, Optional

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from .exceptions import MemoryRetrievalError, MemoryStorageError

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Utility class for managing Zep memory operations.
    
    Provides helper methods for common memory tasks like batch storage,
    memory search, and thread management.
    """

    def __init__(self, client: AsyncZep, user_id: str):
        """
        Initialize MemoryManager.
        
        Args:
            client: AsyncZep client instance
            user_id: User ID for memory operations
        """
        self.client = client
        self.user_id = user_id

    async def create_thread_if_not_exists(self, thread_id: str) -> bool:
        """
        Create a thread if it doesn't exist.
        
        Args:
            thread_id: Thread ID to create
            
        Returns:
            True if thread was created, False if it already existed
            
        Raises:
            MemoryStorageError: If thread creation fails
        """
        try:
            # Try to get the thread first
            await self.client.thread.get(thread_id)
            return False  # Thread already exists
        except Exception:
            # Thread doesn't exist, create it
            try:
                await self.client.thread.create(thread_id=thread_id, user_id=self.user_id)
                logger.info(f"Created thread {thread_id} for user {self.user_id}")
                return True
            except Exception as e:
                raise MemoryStorageError(f"Failed to create thread {thread_id}: {e}")

    async def store_conversation_batch(
        self, thread_id: str, messages: List[Dict[str, str]], max_batch_size: int = 10
    ) -> int:
        """
        Store multiple conversation messages in batches.
        
        Args:
            thread_id: Thread to store messages in
            messages: List of message dicts with 'content', 'role', and optional 'name'
            max_batch_size: Maximum messages per batch
            
        Returns:
            Number of messages successfully stored
            
        Raises:
            MemoryStorageError: If storage fails
        """
        if not messages:
            return 0

        try:
            stored_count = 0
            
            # Process in batches
            for i in range(0, len(messages), max_batch_size):
                batch = messages[i : i + max_batch_size]
                
                # Convert to Zep message format
                zep_messages = []
                for msg in batch:
                    zep_message = Message(
                        content=msg["content"],
                        role=msg.get("role", "user"),
                        name=msg.get("name"),
                    )
                    zep_messages.append(zep_message)

                # Store batch
                await self.client.thread.add_messages(
                    thread_id=thread_id, messages=zep_messages
                )
                stored_count += len(zep_messages)
                
                logger.debug(f"Stored batch of {len(zep_messages)} messages")

            logger.info(f"Stored {stored_count} messages in thread {thread_id}")
            return stored_count

        except Exception as e:
            raise MemoryStorageError(f"Failed to store conversation batch: {e}")

    async def search_relevant_memories(
        self, query: str, limit: int = 5, include_thread_context: bool = True, thread_id: Optional[str] = None
    ) -> Dict[str, List[str]]:
        """
        Search for relevant memories across both graph and thread.
        
        Args:
            query: Search query
            limit: Maximum results to return
            include_thread_context: Whether to include thread context
            thread_id: Optional thread ID for thread-specific context
            
        Returns:
            Dict with 'facts', 'episodes', 'nodes', and 'thread_context' keys
            
        Raises:
            MemoryRetrievalError: If search fails
        """
        try:
            results = {
                "facts": [],
                "episodes": [],
                "nodes": [],
                "thread_context": "",
            }

            # Search user graph
            try:
                graph_results = await self.client.graph.search(
                    user_id=self.user_id, query=query, limit=limit
                )

                # Extract edges (facts/relationships)
                if graph_results.edges:
                    results["facts"] = [edge.fact for edge in graph_results.edges]

                # Extract episodes (conversation snippets)
                if graph_results.episodes:
                    results["episodes"] = [episode.content for episode in graph_results.episodes]

                # Extract nodes (entities)
                if graph_results.nodes:
                    results["nodes"] = [
                        f"{node.name}: {node.summary}" for node in graph_results.nodes
                    ]

            except Exception as e:
                logger.warning(f"Graph search failed: {e}")

            # Get thread context if requested
            if include_thread_context and thread_id:
                try:
                    memory_result = await self.client.thread.get_user_context(thread_id=thread_id)
                    if memory_result.context:
                        results["thread_context"] = memory_result.context
                except Exception as e:
                    logger.warning(f"Thread context retrieval failed: {e}")

            return results

        except Exception as e:
            raise MemoryRetrievalError(f"Memory search failed: {e}")

    async def add_facts_to_graph(self, facts: List[str], data_type: str = "text") -> int:
        """
        Add multiple facts to the user's knowledge graph.
        
        Args:
            facts: List of facts/knowledge to add
            data_type: Type of data (text, json)
            
        Returns:
            Number of facts successfully added
            
        Raises:
            MemoryStorageError: If storage fails
        """
        if not facts:
            return 0

        try:
            added_count = 0
            
            for fact in facts:
                if len(fact.strip()) > 5:  # Only add substantial facts
                    try:
                        await self.client.graph.add(
                            user_id=self.user_id, type=data_type, data=fact
                        )
                        added_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to add fact to graph: {e}")

            logger.info(f"Added {added_count} facts to user graph")
            return added_count

        except Exception as e:
            raise MemoryStorageError(f"Failed to add facts to graph: {e}")

    async def get_thread_summary(self, thread_id: str, max_messages: int = 20) -> Optional[str]:
        """
        Get a summary of recent thread activity.
        
        Args:
            thread_id: Thread to summarize
            max_messages: Maximum messages to include in summary
            
        Returns:
            Thread summary or None if unavailable
            
        Raises:
            MemoryRetrievalError: If retrieval fails
        """
        try:
            memory_result = await self.client.thread.get_user_context(thread_id=thread_id)
            
            if not memory_result.messages:
                return None

            # Get recent messages
            recent_messages = memory_result.messages[-max_messages:]
            
            # Create summary
            summary_lines = []
            for msg in recent_messages:
                summary_lines.append(f"{msg.role}: {msg.content[:100]}...")
                
            return "\n".join(summary_lines)

        except Exception as e:
            raise MemoryRetrievalError(f"Failed to get thread summary: {e}")


def format_memory_context(
    facts: List[str],
    episodes: List[str],
    nodes: List[str],
    thread_context: str = "",
    max_items: int = 3,
) -> str:
    """
    Format memory results into a context string for injection.
    
    Args:
        facts: List of facts from graph
        episodes: List of episodes from graph
        nodes: List of nodes from graph
        thread_context: Thread context string
        max_items: Maximum items per category
        
    Returns:
        Formatted memory context string
    """
    context_parts = []

    if thread_context:
        context_parts.append(f"Conversation context: {thread_context}")

    if facts:
        fact_list = "\n".join([f"- {fact}" for fact in facts[:max_items]])
        context_parts.append(f"Relevant facts:\n{fact_list}")

    if episodes:
        episode_list = "\n".join([f"- {episode}" for episode in episodes[:max_items]])
        context_parts.append(f"Past interactions:\n{episode_list}")

    if nodes:
        node_list = "\n".join([f"- {node}" for node in nodes[:max_items]])
        context_parts.append(f"Known entities:\n{node_list}")

    return "\n\n".join(context_parts) if context_parts else ""