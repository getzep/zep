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
# Size limits (Zep API constraints)
# ---------------------------------------------------------------------------
# Zep rejects messages longer than 4096 characters and graph.add payloads
# longer than 10,000 characters. We truncate slightly below those limits and
# warn (with lengths only, never content) rather than silently dropping data
# or letting the API reject the whole call.
MESSAGE_MAX_CHARS = 4000
GRAPH_MAX_CHARS = 9900

# Valid Zep message roles (subset of zep_cloud RoleType that makes sense for
# agent-authored memory). Unknown roles map to "assistant".
_VALID_ROLES = frozenset({"system", "assistant", "user", "function", "tool"})


def _truncate(text: str, limit: int, label: str) -> str:
    """Truncate *text* to *limit* chars, warning with lengths only (never content)."""
    if len(text) > limit:
        logger.warning(
            "%s exceeds Zep limit; truncating (original_len=%d, limit=%d)",
            label,
            len(text),
            limit,
        )
        return text[:limit]
    return text


def _validate_role(role: str) -> str:
    """Map an arbitrary role string onto a valid Zep RoleType, defaulting to 'assistant'."""
    normalized = (role or "").strip().lower()
    if normalized in _VALID_ROLES:
        return normalized
    logger.warning("Unknown message role; defaulting to 'assistant'")
    return "assistant"


# ---------------------------------------------------------------------------
# Background event loop + shared client
# ---------------------------------------------------------------------------
# AG2 executes tool functions synchronously (even when declared async). When the
# caller is already inside an asyncio event loop (e.g. the test's
# ``asyncio.run(main())``), ``asyncio.run()`` / ``loop.run_until_complete()``
# raise. On Python 3.13 ``asyncio.get_event_loop()`` raises outright when there
# is no running loop.
#
# AsyncZep wraps an ``httpx.AsyncClient`` which binds to whichever event loop
# first drives a request and must thereafter be used only on that loop.
#
# Solution: keep ONE dedicated daemon background event loop alive on its own
# thread for the lifetime of the process. All async Zep work — for both the
# sync tool wrappers and the manager classes' sync wrappers — is submitted to
# that loop via ``run_coroutine_threadsafe``. The caller-supplied AsyncZep is
# reused directly (no per-call construction, no private-attribute access); it
# becomes bound to the background loop on first use and is driven only there.
# ---------------------------------------------------------------------------

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background event loop, lazily creating it (thread-safe)."""
    global _bg_loop
    with _bg_loop_lock:
        if _bg_loop is None or _bg_loop.is_closed():
            loop = asyncio.new_event_loop()

            def _run_loop(lp: asyncio.AbstractEventLoop) -> None:
                asyncio.set_event_loop(lp)
                lp.run_forever()

            thread = threading.Thread(
                target=_run_loop, args=(loop,), name="zep-ag2-bg-loop", daemon=True
            )
            thread.start()
            _bg_loop = loop
        return _bg_loop


def _run_sync(coro: Any) -> Any:
    """Run *coro* on the shared background loop and block until it completes.

    Safe to call from any thread, including from inside another running event
    loop (AG2's execution context), and works on Python 3.11–3.13 where
    ``asyncio.get_event_loop()`` raises with no running loop.
    """
    loop = _get_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def shutdown_background_loop() -> None:
    """Stop and close the shared background event loop, if running.

    Optional cleanup helper for hosts that want a deterministic shutdown. The
    loop is recreated on the next tool/manager sync call. Does not close any
    caller-supplied Zep client (the caller owns its lifecycle).
    """
    global _bg_loop
    with _bg_loop_lock:
        loop = _bg_loop
        _bg_loop = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


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
        client: An initialized AsyncZep instance (reused for all calls).
        user_id: The user ID to search memories for.
        session_id: Optional thread/session ID for thread-scoped search.

    Returns:
        A callable suitable for AG2 tool registration.
    """

    async def _search(query: str, limit: int, scope: str | None) -> str:
        try:
            results = await client.graph.search(
                user_id=user_id,
                query=query,
                limit=limit,
                scope=scope,
            )
            return _format_graph_results(results)
        except Exception as e:
            logger.error("Zep search_memory failed: %s", type(e).__name__)
            return "Error: unable to search memory at this time."

    def search_memory(
        query: Annotated[str, "The search query to find relevant memories"],
        limit: Annotated[int, "Maximum number of results to return"] = 5,
        scope: Annotated[
            str | None,
            "Scope of search: 'edges' (facts), 'nodes' (entities), or 'episodes'",
        ] = "edges",
    ) -> str:
        """Search Zep memory for relevant information based on a query."""
        return str(_run_sync(_search(query, limit, scope)))

    return search_memory


def create_add_memory_tool(
    client: AsyncZep,
    user_id: str,
    session_id: str | None = None,
) -> Any:
    """
    Create a tool function for adding messages to Zep conversation memory.

    The returned function stores messages in a Zep thread. If no session_id is
    provided at creation time, the content is added to the user's knowledge graph.

    Args:
        client: An initialized AsyncZep instance (reused for all calls).
        user_id: The user ID who owns the memory.
        session_id: Optional thread/session ID for storing messages.

    Returns:
        A callable suitable for AG2 tool registration.
    """
    from zep_cloud.types import Message

    async def _add(content: str, role: str) -> str:
        if not session_id:
            try:
                await client.graph.add(
                    user_id=user_id,
                    type="text",
                    data=_truncate(content, GRAPH_MAX_CHARS, "graph data"),
                )
                return "Memory added to knowledge graph successfully."
            except Exception as e:
                logger.error("Zep add_memory (graph) failed: %s", type(e).__name__)
                return "Error: unable to add memory at this time."

        try:
            message = Message(
                content=_truncate(content, MESSAGE_MAX_CHARS, "message content"),
                role=_validate_role(role),
            )
            await client.thread.add_messages(
                thread_id=session_id,
                messages=[message],
            )
            return "Memory added to conversation thread successfully."
        except Exception as e:
            logger.error("Zep add_memory (thread) failed: %s", type(e).__name__)
            return "Error: unable to add memory at this time."

    def add_memory(
        content: Annotated[str, "The content to store as a memory"],
        role: Annotated[
            str, "The role of the message sender (user, assistant, or system)"
        ] = "assistant",
    ) -> str:
        """Add a message to Zep conversation memory."""
        return str(_run_sync(_add(content, role)))

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
        client: An initialized AsyncZep instance (reused for all calls).
        user_id: User ID for user knowledge graph search.
        graph_id: Graph ID for named knowledge graph search.

    Returns:
        A callable suitable for AG2 tool registration.

    Raises:
        ZepAG2MemoryError: If neither or both user_id and graph_id are provided.
    """
    if not user_id and not graph_id:
        raise ZepAG2MemoryError("Either user_id or graph_id must be provided")
    if user_id and graph_id:
        raise ZepAG2MemoryError("Only one of user_id or graph_id should be provided")

    async def _search_g(query: str, limit: int, scope: str | None) -> str:
        try:
            kwargs: dict[str, Any] = {"query": query, "limit": limit, "scope": scope}
            if graph_id:
                kwargs["graph_id"] = graph_id
            else:
                kwargs["user_id"] = user_id

            results = await client.graph.search(**kwargs)
            return _format_graph_results(results)
        except Exception as e:
            logger.error("Zep search_graph failed: %s", type(e).__name__)
            return "Error: unable to search the knowledge graph at this time."

    def search_graph(
        query: Annotated[str, "The search query for the knowledge graph"],
        limit: Annotated[int, "Maximum number of results to return"] = 5,
        scope: Annotated[
            str | None,
            "Scope of search: 'edges' (facts), 'nodes' (entities), or 'episodes'",
        ] = "edges",
    ) -> str:
        """Search the Zep knowledge graph for relevant information."""
        return str(_run_sync(_search_g(query, limit, scope)))

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
        client: An initialized AsyncZep instance (reused for all calls).
        user_id: User ID for user knowledge graph storage.
        graph_id: Graph ID for named knowledge graph storage.

    Returns:
        A callable suitable for AG2 tool registration.

    Raises:
        ZepAG2MemoryError: If neither or both user_id and graph_id are provided.
    """
    if not user_id and not graph_id:
        raise ZepAG2MemoryError("Either user_id or graph_id must be provided")
    if user_id and graph_id:
        raise ZepAG2MemoryError("Only one of user_id or graph_id should be provided")

    async def _add_graph(data: str, data_type: str) -> str:
        try:
            kwargs: dict[str, Any] = {
                "type": data_type,
                "data": _truncate(data, GRAPH_MAX_CHARS, "graph data"),
            }
            if graph_id:
                kwargs["graph_id"] = graph_id
            else:
                kwargs["user_id"] = user_id

            await client.graph.add(**kwargs)

            target = f"graph '{graph_id}'" if graph_id else f"user '{user_id}'"
            return f"Data added to knowledge graph for {target} successfully."
        except Exception as e:
            logger.error("Zep add_graph_data failed: %s", type(e).__name__)
            return "Error: unable to add data to the knowledge graph at this time."

    def add_graph_data(
        data: Annotated[str, "Text data to add to the knowledge graph"],
        data_type: Annotated[str, "Type of data: 'text', 'json', or 'message'"] = "text",
    ) -> str:
        """Add data to the Zep knowledge graph."""
        return str(_run_sync(_add_graph(data, data_type)))

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
        client: An initialized AsyncZep instance (reused for all calls).
        user_id: The user ID for memory operations.
        session_id: Optional thread/session ID for conversation memory.
        graph_id: Optional graph ID for named knowledge graph operations.

    Returns:
        A dict mapping tool names to their callable functions.
    """
    tools: dict[str, Any] = {}

    # Graph tools — use graph_id if provided, otherwise user_id
    target_user_id = None if graph_id else user_id

    factories = [
        ("search_memory", create_search_memory_tool(client, user_id, session_id)),
        ("add_memory", create_add_memory_tool(client, user_id, session_id)),
        (
            "search_graph",
            create_search_graph_tool(client, user_id=target_user_id, graph_id=graph_id),
        ),
        (
            "add_graph_data",
            create_add_graph_data_tool(client, user_id=target_user_id, graph_id=graph_id),
        ),
    ]

    descriptions = {
        "search_memory": "Search conversation memory for relevant information",
        "add_memory": "Add a message to conversation memory",
        "search_graph": "Search the knowledge graph for relevant information",
        "add_graph_data": "Add data to the knowledge graph",
    }

    for name, fn in factories:
        # Register for LLM (declaration) and execution exactly once each. AG2
        # warns with "Function is being overridden" if the same name is
        # registered for execution more than once, so we register once per tool.
        agent.register_for_llm(description=descriptions[name])(fn)
        executor.register_for_execution()(fn)
        tools[name] = fn

    return tools
