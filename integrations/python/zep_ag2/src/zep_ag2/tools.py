"""
Zep AG2 Tool Factories.

This module provides factory functions that create AG2-compatible tool functions
for interacting with Zep memory and knowledge graph operations.

AG2 uses decorator-based tool registration (@register_for_llm / @register_for_execution)
with typing.Annotated for parameter descriptions. The factories here return plain
callables that are compatible with this pattern.
"""

import asyncio
import logging
import threading
from typing import Annotated, Any

from zep_cloud.client import AsyncZep

from zep_ag2.exceptions import ZepAG2MemoryError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-loop helper
# ---------------------------------------------------------------------------
# AG2 executes tool functions synchronously (even if they are declared async).
# When the caller is already inside an asyncio event loop (e.g. the test's
# ``asyncio.run(main())``), calling ``asyncio.run()`` or
# ``loop.run_until_complete()`` from within that loop raises:
#   "This event loop is already running" / "bound to a different event loop"
#
# Additionally, AsyncZep (via httpx.AsyncClient) is bound to the event loop
# in which it was created. Running its coroutines in a *different* loop raises
#   "Event object is bound to a different event loop"
#
# Solution: keep a dedicated background event loop alive on its own thread.
# Each tool factory creates its *own* AsyncZep instance inside that loop via
# ``_make_client_in_bg_loop()``, so the client is always used in the loop it
# was created in.
# ---------------------------------------------------------------------------

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background event loop, creating it on first call."""
    global _bg_loop
    with _bg_loop_lock:
        if _bg_loop is None or _bg_loop.is_closed():
            loop = asyncio.new_event_loop()
            _bg_loop = loop

            def _run_loop(lp: asyncio.AbstractEventLoop) -> None:
                asyncio.set_event_loop(lp)
                lp.run_forever()

            t = threading.Thread(target=_run_loop, args=(loop,), daemon=True)
            t.start()
        return _bg_loop


def _run_async(coro: Any) -> Any:
    """Run *coro* on the shared background loop and block until it finishes."""
    loop = _get_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def _format_graph_results(search_results: Any) -> str:
    """Format Zep graph search results into a human-readable string."""
    parts: list[str] = []

    if search_results.edges:
        for edge in search_results.edges:
            parts.append(f"- Fact: {edge.fact}")

    if search_results.nodes:
        for node in search_results.nodes:
            summary = node.summary or "No summary"
            parts.append(f"- Entity: {node.name}: {summary}")

    if search_results.episodes:
        for episode in search_results.episodes:
            parts.append(f"- Episode: {episode.content}")

    if not parts:
        return "No results found."

    return "\n".join(parts)


def create_search_memory_tool(
    client: AsyncZep,
    user_id: str,
    session_id: str | None = None,
) -> Any:
    """
    Create a tool function for searching Zep conversation memory.

    The returned function searches the user's knowledge graph for relevant memories.
    Compatible with AG2's @register_for_llm / @register_for_execution decorators.

    Args:
        client: An initialized AsyncZep instance.
        user_id: The user ID to search memories for.
        session_id: Optional thread/session ID for thread-scoped search.

    Returns:
        A callable suitable for AG2 tool registration.
    """
    api_key: str = client._client_wrapper.api_key

    async def _search(query: str, limit: int) -> str:
        _client = AsyncZep(api_key=api_key)
        try:
            results = await _client.graph.search(
                user_id=user_id,
                query=query,
                limit=limit,
            )
            return _format_graph_results(results)
        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return f"Error searching memory: {e}"

    def search_memory(
        query: Annotated[str, "The search query to find relevant memories"],
        limit: Annotated[int, "Maximum number of results to return"] = 5,
    ) -> str:
        """Search Zep memory for relevant information based on a query."""
        return str(_run_async(_search(query, limit)))

    return search_memory


def create_add_memory_tool(
    client: AsyncZep,
    user_id: str,
    session_id: str | None = None,
) -> Any:
    """
    Create a tool function for adding messages to Zep conversation memory.

    The returned function stores messages in a Zep thread. If no session_id is
    provided at creation time, a thread_id must be set before use.

    Args:
        client: An initialized AsyncZep instance.
        user_id: The user ID who owns the memory.
        session_id: Optional thread/session ID for storing messages.

    Returns:
        An async callable suitable for AG2 tool registration.
    """
    from zep_cloud.types import Message

    api_key: str = client._client_wrapper.api_key

    async def _add(content: str, role: str) -> str:
        _client = AsyncZep(api_key=api_key)
        if not session_id:
            try:
                await _client.graph.add(
                    user_id=user_id,
                    type="text",
                    data=content,
                )
                return "Memory added to knowledge graph successfully."
            except Exception as e:
                logger.error(f"Error adding memory to graph: {e}")
                return f"Error adding memory: {e}"

        try:
            message = Message(content=content, role=role)
            await _client.thread.add_messages(
                thread_id=session_id,
                messages=[message],
            )
            return "Memory added to conversation thread successfully."
        except Exception as e:
            logger.error(f"Error adding memory: {e}")
            return f"Error adding memory: {e}"

    def add_memory(
        content: Annotated[str, "The content to store as a memory"],
        role: Annotated[
            str, "The role of the message sender (user, assistant, or system)"
        ] = "assistant",
    ) -> str:
        """Add a message to Zep conversation memory."""
        return str(_run_async(_add(content, role)))

    return add_memory


def create_search_graph_tool(
    client: AsyncZep,
    user_id: str | None = None,
    graph_id: str | None = None,
) -> Any:
    """
    Create a tool function for searching the Zep knowledge graph.

    Exactly one of user_id or graph_id must be provided.

    Args:
        client: An initialized AsyncZep instance.
        user_id: User ID for user knowledge graph search.
        graph_id: Graph ID for named knowledge graph search.

    Returns:
        An async callable suitable for AG2 tool registration.

    Raises:
        ZepAG2ConfigError: If neither or both user_id and graph_id are provided.
    """
    if not user_id and not graph_id:
        raise ZepAG2MemoryError("Either user_id or graph_id must be provided")
    if user_id and graph_id:
        raise ZepAG2MemoryError("Only one of user_id or graph_id should be provided")

    api_key: str = client._client_wrapper.api_key

    async def _search_g(query: str, limit: int, scope: str | None) -> str:
        _client = AsyncZep(api_key=api_key)
        try:
            kwargs: dict[str, Any] = {"query": query, "limit": limit, "scope": scope}
            if graph_id:
                kwargs["graph_id"] = graph_id
            else:
                kwargs["user_id"] = user_id

            results = await _client.graph.search(**kwargs)
            return _format_graph_results(results)
        except Exception as e:
            logger.error(f"Error searching graph: {e}")
            return f"Error searching knowledge graph: {e}"

    def search_graph(
        query: Annotated[str, "The search query for the knowledge graph"],
        limit: Annotated[int, "Maximum number of results to return"] = 5,
        scope: Annotated[
            str | None,
            "Scope of search: 'edges' (facts), 'nodes' (entities), or 'episodes'",
        ] = "edges",
    ) -> str:
        """Search the Zep knowledge graph for relevant information."""
        return str(_run_async(_search_g(query, limit, scope)))

    return search_graph


def create_add_graph_data_tool(
    client: AsyncZep,
    user_id: str | None = None,
    graph_id: str | None = None,
) -> Any:
    """
    Create a tool function for adding data to the Zep knowledge graph.

    Exactly one of user_id or graph_id must be provided.

    Args:
        client: An initialized AsyncZep instance.
        user_id: User ID for user knowledge graph storage.
        graph_id: Graph ID for named knowledge graph storage.

    Returns:
        An async callable suitable for AG2 tool registration.

    Raises:
        ZepAG2ConfigError: If neither or both user_id and graph_id are provided.
    """
    if not user_id and not graph_id:
        raise ZepAG2MemoryError("Either user_id or graph_id must be provided")
    if user_id and graph_id:
        raise ZepAG2MemoryError("Only one of user_id or graph_id should be provided")

    api_key: str = client._client_wrapper.api_key

    async def _add_graph(data: str, data_type: str) -> str:
        _client = AsyncZep(api_key=api_key)
        try:
            kwargs: dict[str, Any] = {"type": data_type, "data": data}
            if graph_id:
                kwargs["graph_id"] = graph_id
            else:
                kwargs["user_id"] = user_id

            await _client.graph.add(**kwargs)

            target = f"graph '{graph_id}'" if graph_id else f"user '{user_id}'"
            return f"Data added to knowledge graph for {target} successfully."
        except Exception as e:
            logger.error(f"Error adding graph data: {e}")
            return f"Error adding data to knowledge graph: {e}"

    def add_graph_data(
        data: Annotated[str, "Text data to add to the knowledge graph"],
        data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
    ) -> str:
        """Add data to the Zep knowledge graph."""
        return str(_run_async(_add_graph(data, data_type)))

    return add_graph_data


def register_all_tools(
    agent: Any,
    executor: Any,
    client: AsyncZep,
    user_id: str,
    session_id: str | None = None,
    graph_id: str | None = None,
) -> dict[str, Any]:
    """
    Create and register all Zep tools on AG2 agents.

    This is a convenience function that creates all available tools and registers
    them using AG2's register_for_llm / register_for_execution pattern.

    Args:
        agent: The AG2 agent that will call the tools (register_for_llm).
        executor: The AG2 agent that will execute the tools (register_for_execution).
        client: An initialized AsyncZep instance.
        user_id: The user ID for memory operations.
        session_id: Optional thread/session ID for conversation memory.
        graph_id: Optional graph ID for named knowledge graph operations.

    Returns:
        A dict mapping tool names to their callable functions.
    """
    tools: dict[str, Any] = {}

    # Search memory tool (always uses user_id for graph search)
    search_mem = create_search_memory_tool(client, user_id, session_id)
    agent.register_for_llm(description="Search conversation memory for relevant information")(
        search_mem
    )
    executor.register_for_execution()(search_mem)
    tools["search_memory"] = search_mem

    # Add memory tool
    add_mem = create_add_memory_tool(client, user_id, session_id)
    agent.register_for_llm(description="Add a message to conversation memory")(add_mem)
    executor.register_for_execution()(add_mem)
    tools["add_memory"] = add_mem

    # Graph tools — use graph_id if provided, otherwise user_id
    target_user_id = None if graph_id else user_id
    target_graph_id = graph_id

    search_graph = create_search_graph_tool(
        client, user_id=target_user_id, graph_id=target_graph_id
    )
    agent.register_for_llm(description="Search the knowledge graph for relevant information")(
        search_graph
    )
    executor.register_for_execution()(search_graph)
    tools["search_graph"] = search_graph

    add_graph = create_add_graph_data_tool(client, user_id=target_user_id, graph_id=target_graph_id)
    agent.register_for_llm(description="Add data to the knowledge graph")(add_graph)
    executor.register_for_execution()(add_graph)
    tools["add_graph_data"] = add_graph

    return tools
