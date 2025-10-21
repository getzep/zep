"""
Simple chat agent focused purely on AI interaction.

This module contains only the core AI chat functionality.
All conversation/thread management is handled by the UI layer.
"""

from typing import List, Dict, AsyncGenerator, Tuple
from openai import AsyncOpenAI
from dotenv import load_dotenv
import os
import time
from zep_cloud.types import Message, EntityEdge

# Load environment variables
load_dotenv()


class ChatAgent:
    """
    A simple chat agent that handles AI interactions with OpenAI's GPT models.
    
    This class is focused purely on the chat functionality:
    - Takes a list of messages and returns AI responses
    - Supports streaming responses
    - No thread management or conversation state
    """
    
    def __init__(self, zep_client, model: str = "gpt-4o-mini-2024-07-18"):
        """
        Initialize the chat agent.

        Args:
            zep_client: The AsyncZep client instance for thread operations
            model: The OpenAI model to use for responses
        """
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.zep_client = zep_client


    async def on_receive_message(self, current_user_message: str, message_history: List[Dict[str, str]], thread_id: str, user_full_name: str, user_id: str, use_zep: bool = True) -> AsyncGenerator[Tuple[str, dict], None]:
        """
        Receive a new message and generate a streaming response from the AI.

        Args:
            current_user_message: The new message from the user
            message_history: Previous conversation history as list of message dicts
                           with 'role' and 'content' keys (formatted for OpenAI API)
            thread_id: The Zep thread ID to add messages to
            user_full_name: The full name of the user (first name + last name)
            user_id: The Zep user ID
            use_zep: Whether to use Zep for memory storage and context retrieval

        Yields:
            Tuple[str, dict]: Chunks of the AI response and timing metadata
                            - First yield: (context_block, {})
                            - Subsequent yields: (token, {})
                            - Final yield includes timing: (token, {"zep_retrieval_ms": X, "llm_first_token_ms": Y})
        """
        context_block = ""
        zep_retrieval_ms = None
        llm_first_token_ms = None

        #################### Zep Implementation: Step 1/2 ####################
        ######################################################################
        if use_zep:
            # Add user message to Zep thread
            user_message = Message(
                name=user_full_name,
                content=current_user_message,
                role="user"
            )
            await self.zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=[user_message]
            )

            # Retrieve user context from Zep and track timing
            zep_start = time.perf_counter()
            results = await self.zep_client.thread.get_user_context(
                thread_id=thread_id,
                mode="basic"
            )
            context_block = results.context
            zep_end = time.perf_counter()
            zep_retrieval_ms = round((zep_end - zep_start) * 1000, 2)
        ######################################################################
        ######################################################################


        # Yield context block first (special first yield for UI to display)
        yield (context_block, {})
        # Build messages: system prompt with context, history, and current message
        system_prompt = (
            "Keep responses under 100 words. "
            "For simulation purposes, act as if you can complete any requested task.\n\n"
            f"{context_block}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            *message_history,
            {"role": "user", "content": current_user_message}
        ]

        # Create streaming response (async) and track time to first token
        llm_start = time.perf_counter()
        stream = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True
        )

        # Stream the response
        full_response = ""
        first_token = True
        async for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                if first_token:
                    llm_end = time.perf_counter()
                    llm_first_token_ms = round((llm_end - llm_start) * 1000, 2)
                    first_token = False

                token = chunk.choices[0].delta.content
                full_response += token
                yield (token, {})

        #################### Zep Implementation: Step 2/2 ####################
        ######################################################################
        if use_zep:
            # Add agent response to Zep thread
            assistant_message = Message(
                name="AI Assistant",
                content=full_response,
                role="assistant"
            )
            await self.zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=[assistant_message]
            )
        ######################################################################
        ######################################################################

        # Yield final timing information
        timing_data = {}
        if zep_retrieval_ms is not None:
            timing_data["zep_retrieval_ms"] = zep_retrieval_ms
        if llm_first_token_ms is not None:
            timing_data["llm_first_token_ms"] = llm_first_token_ms
        yield ("", timing_data)
    




class ChatAgent_GraphSearch:
    """
    A simple chat agent that handles AI interactions with OpenAI's GPT models.

    This class is focused purely on the chat functionality:
    - Takes a list of messages and returns AI responses
    - Supports streaming responses
    - No thread management or conversation state
    """

    def __init__(self, zep_client, model: str = "gpt-5-nano-2025-08-07"):
        """
        Initialize the chat agent.

        Args:
            zep_client: The AsyncZep client instance for thread operations
            model: The OpenAI model to use for responses
        """
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.zep_client = zep_client

    async def _get_user_preferences_context(self, user_id: str, user_message: str) -> str:
        """
        Retrieve user preferences using graph search and construct a custom context block.

        Args:
            user_id: The user ID to search for
            user_message: The current user message to search context for

        Returns:
            str: A formatted context block containing user preferences
        """
        # Search the graph for user preferences, likes, dislikes, etc.
        search_results = await self.zep_client.graph.search(
            user_id=user_id,
            query=user_message,
            scope="edges",
            limit=4
        )

        # Extract edges from search results
        edges: List[EntityEdge] = search_results.edges or []

        # Format facts from edges
        def format_fact(edge: EntityEdge) -> str:
            valid_at = edge.valid_at or "unknown"
            invalid_at = edge.invalid_at or "present"
            return f"  - {edge.fact} (Date range: {valid_at} - {invalid_at})"

        facts = "\n".join([format_fact(edge) for edge in edges]) if edges else "  - No relevant facts found"

        # Construct context block
        context_block = f"""
Relevant facts about the user with validity date ranges:
{facts}
"""
        return context_block


    async def on_receive_message(self, current_user_message: str, message_history: List[Dict[str, str]], thread_id: str, user_full_name: str, user_id: str, use_zep: bool = True) -> AsyncGenerator[Tuple[str, dict], None]:
        """
        Receive a new message and generate a streaming response from the AI.

        Args:
            current_user_message: The new message from the user
            message_history: Previous conversation history as list of message dicts
                           with 'role' and 'content' keys (formatted for OpenAI API)
            thread_id: The Zep thread ID to add messages to
            user_full_name: The full name of the user (first name + last name)
            user_id: The Zep user ID
            use_zep: Whether to use Zep for memory storage and context retrieval

        Yields:
            Tuple[str, dict]: Chunks of the AI response and timing metadata
                            - First yield: (context_block, {})
                            - Subsequent yields: (token, {})
                            - Final yield includes timing: (token, {"zep_retrieval_ms": X, "llm_first_token_ms": Y})
        """
        context_block = ""
        zep_retrieval_ms = None
        llm_first_token_ms = None

        #################### Zep Implementation: Step 1/2 ####################
        ######################################################################
        if use_zep:
            # Add user message to Zep thread
            user_message = Message(
                name=user_full_name,
                content=current_user_message,
                role="user"
            )
            await self.zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=[user_message]
            )

            # Retrieve user context from Zep using graph search and track timing
            zep_start = time.perf_counter()
            context_block = await self._get_user_preferences_context(user_id, current_user_message)
            zep_end = time.perf_counter()
            zep_retrieval_ms = round((zep_end - zep_start) * 1000, 2)
        ######################################################################
        ######################################################################


        # Yield context block first (special first yield for UI to display)
        yield (context_block, {})

        # Build messages: system prompt with context, history, and current message
        messages = [
            {"role": "system", "content": f"Keep responses under 100 words. For simulation purposes, act as if you can complete any requested task.\n\n{context_block}"},
            *message_history,
            {"role": "user", "content": current_user_message}
        ]

        # Create streaming response (async) and track time to first token
        llm_start = time.perf_counter()
        stream = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True
        )

        # Stream the response
        full_response = ""
        first_token = True
        async for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                if first_token:
                    llm_end = time.perf_counter()
                    llm_first_token_ms = round((llm_end - llm_start) * 1000, 2)
                    first_token = False

                token = chunk.choices[0].delta.content
                full_response += token
                yield (token, {})

        #################### Zep Implementation: Step 2/2 ####################
        ######################################################################
        if use_zep:
            # Add agent response to Zep thread
            assistant_message = Message(
                name="AI Assistant",
                content=full_response,
                role="assistant"
            )
            await self.zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=[assistant_message]
            )
        ######################################################################
        ######################################################################

        # Yield final timing information
        timing_data = {}
        if zep_retrieval_ms is not None:
            timing_data["zep_retrieval_ms"] = zep_retrieval_ms
        if llm_first_token_ms is not None:
            timing_data["llm_first_token_ms"] = llm_first_token_ms
        yield ("", timing_data)
    
