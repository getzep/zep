"""
Zep AG2 Tool Factories.

This module provides factory functions that create AG2-compatible tool functions
for interacting with Zep memory and knowledge graph operations.

AG2 uses decorator-based tool registration (@register_for_llm / @register_for_execution)
with typing.Annotated for parameter descriptions. The factories here return plain
callables that are compatible with this pattern.

``create_search_graph_tool`` and ``create_search_memory_tool`` follow the
pin-or-expose pattern shared by the other Zep framework integrations: every
search parameter (``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
``center_node_uuid``) is exposed to the model by default and can be pinned
(fixed to a constant, hidden from the model) or hidden (removed from the
schema without pinning; Zep's own default applies) at construction time.

Zep v4 addresses every graph by its server-generated UUID, and it replaces the
single v3 ``graph.search`` method with one method for each scope
(``graph.search_edges``, ``graph.search_nodes``, ``graph.search_episodes``,
``graph.search_observations``, ``graph.search_thread_summaries``) plus
``graph.get_context`` for the ``auto`` scope. The ``scope`` parameter stays in
the tool schema and selects the v4 method.

AG2's ``Tool``/``register_for_llm`` derives its schema from the wrapped
function's typed signature (``inspect.signature`` + ``typing.get_type_hints``,
verified against the installed ``ag2`` package's ``autogen.tools.tool.Tool``
and ``autogen.tools.function_utils.get_function_schema``) -- the same
approach as AutoGen's ``FunctionTool``. So pin-or-expose here works the same
way as ``zep_autogen.tools``: exposed parameters become real, typed
parameters of a dynamically-built ``inspect.Signature`` assigned to the
function's ``__signature__``/``__annotations__``, while pinned/hidden
parameters are never parameters of the function at all -- they are merged in
as constants (or omitted) when the tool actually calls the v4 search method.
"""

import asyncio
import inspect
import logging
import threading
from typing import Annotated, Any, Literal

from zep_cloud.client import AsyncZep

from zep_ag2.exceptions import ZepAG2MemoryError

logger = logging.getLogger(__name__)

Scope = Literal[
    "edges",
    "nodes",
    "episodes",
    "observations",
    "thread_summaries",
    "auto",
]
Reranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Zep caps the search ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a graph search parameter that can be pinned or exposed
# to the model.  Keys match the kwargs of the Zep v4 graph search methods.  Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``hidden_params``. ``annotation`` is the real typed annotation used to build
# the dynamic function signature AG2's ``Tool`` introspects.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "annotation": Scope,
        "description": (
            "What to search for: 'edges' for facts and relationships, "
            "'nodes' for entities and their summaries, "
            "'episodes' for raw text data (unstructured text, messages, or JSON), "
            "'observations' for derived memories, "
            "'thread_summaries' for incremental thread summaries, "
            "'auto' to let Zep decide the best mix of results."
        ),
        "default": "edges",
    },
    "reranker": {
        "annotation": Reranker,
        "description": (
            "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), "
            "'cross_encoder' (highest accuracy), 'episode_mentions' "
            "(frequently referenced), 'node_distance' (near a specific entity)."
        ),
        "default": "rrf",
    },
    "limit": {
        "annotation": int,
        "description": "Maximum number of results to return.",
        "default": 10,
    },
    "mmr_lambda": {
        "annotation": float | None,
        "description": (
            "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'."
        ),
        "default": None,
    },
    "center_node_uuid": {
        "annotation": str | None,
        "description": (
            "UUID of the center node for distance-based reranking. "
            "Required when reranker is 'node_distance'."
        ),
        "default": None,
    },
}

#: Parameters that are always constructor-only (complex types not suitable for
#: model schema generation).
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def _build_search_signature(
    exposed: dict[str, dict[str, Any]],
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build the typed signature AG2's ``Tool`` will introspect.

    ``query`` is always present and required; ``exposed`` params (those not
    pinned or hidden) become real, defaulted parameters annotated with
    ``Annotated[<type>, <description>]`` so the schema generation picks up
    both the type/enum and the description.
    """
    params: list[inspect.Parameter] = [
        inspect.Parameter(
            "query",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Annotated[str, "Search query text (max 400 characters)."],
        )
    ]
    for name, spec in exposed.items():
        base_type = spec["annotation"]
        param_description = spec["description"]
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=spec["default"],
                annotation=Annotated[base_type, param_description],
            )
        )
    signature = inspect.Signature(params)
    annotations = {p.name: p.annotation for p in params}
    return signature, annotations


def _resolve_pinned_and_hidden(
    *,
    pinned_params: dict[str, Any] | None,
    hidden_params: set[str] | None,
    scope: str | None,
    limit: int | None,
) -> tuple[dict[str, Any], set[str]]:
    """Merge explicit pin/hide args with legacy back-compat constructor args."""
    pinned: dict[str, Any] = dict(pinned_params or {})
    hidden: set[str] = set(hidden_params or ())

    # Legacy constructor args pin (and thus hide) their parameter, same as
    # passing it via pinned_params -- back-compat for the pre-pin-or-expose API.
    if scope is not None:
        pinned.setdefault("scope", scope)
    if limit is not None:
        pinned.setdefault("limit", limit)

    unknown_pinned = set(pinned.keys()) - _PINNABLE_PARAMS
    if unknown_pinned:
        raise ValueError(
            f"Unknown pinned parameters: {unknown_pinned}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )
    unknown_hidden = hidden - _PINNABLE_PARAMS
    if unknown_hidden:
        raise ValueError(
            f"Unknown hidden parameters: {unknown_hidden}. Allowed: {sorted(_PINNABLE_PARAMS)}"
        )

    # Clamp a pinned limit to Zep's ceiling at construction time so the call
    # never 400s.
    if "limit" in pinned:
        pinned_limit = pinned["limit"]
        if pinned_limit > MAX_SEARCH_LIMIT:
            logger.warning(
                "search limit %d exceeds Zep ceiling %d; clamping to %d",
                pinned_limit,
                MAX_SEARCH_LIMIT,
                MAX_SEARCH_LIMIT,
            )
            pinned["limit"] = MAX_SEARCH_LIMIT
        elif pinned_limit < 1:
            pinned["limit"] = 1

    # Auto scope rejects node_distance / episode_mentions and ignores reranker
    # entirely.  If scope is pinned to "auto" and reranker is also pinned,
    # resolve the effective value once, here, so the call path is always valid.
    if pinned.get("scope") == "auto" and "reranker" in pinned:
        if pinned["reranker"] in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                pinned["reranker"],
            )
        del pinned["reranker"]
        # Keep the param out of the model schema, as pinning intended.
        hidden.add("reranker")

    return pinned, hidden


def _build_search_kwargs(
    call_args: dict[str, Any],
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    constructor_only: dict[str, Any],
) -> dict[str, Any]:
    """Merge pinned / model-provided / default parameters for one search call.

    A param pinned or hidden is never read from ``call_args``. A param that
    is neither pinned nor supplied by the model is omitted entirely -- in
    particular, ``mmr_lambda``/``center_node_uuid`` (whose spec default is
    ``None``) are never forwarded as an explicit ``None``, so Zep's own
    server-side default applies instead of an explicit null on the wire.
    """
    query = str(call_args.get("query", ""))[:400]
    search_kwargs: dict[str, Any] = {"query": query}

    for param_name in _SEARCH_PARAM_SPECS:
        if param_name in pinned:
            search_kwargs[param_name] = pinned[param_name]
        elif param_name in hidden:
            continue  # hidden, not pinned -> omit; Zep applies its own default
        elif param_name in call_args:
            value = call_args[param_name]
            if value is not None:
                search_kwargs[param_name] = value

    # Clamp a model-provided limit to [1, MAX_SEARCH_LIMIT] so the call never
    # 400s (a pinned limit is already clamped at construction time).
    limit_value = search_kwargs.get("limit")
    if isinstance(limit_value, int):
        if limit_value > MAX_SEARCH_LIMIT:
            logger.warning(
                "search limit %d exceeds Zep ceiling %d; clamping to %d",
                limit_value,
                MAX_SEARCH_LIMIT,
                MAX_SEARCH_LIMIT,
            )
            search_kwargs["limit"] = MAX_SEARCH_LIMIT
        elif limit_value < 1:
            search_kwargs["limit"] = 1

    if search_kwargs.get("scope") == "auto" and "reranker" in search_kwargs:
        # Auto search always uses RRF internally and ignores reranker
        # entirely; Zep rejects node_distance/episode_mentions outright.
        # Warn only when the (would-be) reranker is one Zep would reject.
        dropped_reranker = search_kwargs.pop("reranker")
        if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "reranker %r is invalid for scope='auto'; omitting reranker.",
                dropped_reranker,
            )

    search_kwargs.update(constructor_only)
    return search_kwargs


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


def _name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and summary as "name: summary", falling back gracefully."""
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""


def _format_graph_results(items: list[Any], scope: str = "edges") -> str:
    """Format the items of a Zep v4 search page into a human-readable string."""
    parts: list[str] = []

    if scope == "edges":
        parts = [f"- Fact: {edge.fact}" for edge in items if getattr(edge, "fact", None)]
    elif scope == "nodes":
        for node in items:
            text = _name_summary_text(getattr(node, "name", None), getattr(node, "summary", None))
            if text:
                parts.append(f"- Entity: {text}")
    elif scope == "episodes":
        parts = [f"- Episode: {ep.content}" for ep in items if getattr(ep, "content", None)]
    elif scope == "observations":
        for obs in items:
            text = _name_summary_text(getattr(obs, "name", None), getattr(obs, "summary", None))
            if text:
                parts.append(f"- Observation: {text}")
    elif scope == "thread_summaries":
        for ts in items:
            summary = getattr(ts, "summary", None) or getattr(ts, "name", None)
            if summary:
                parts.append(f"- {summary}")

    return "\n".join(parts) if parts else "No results found."


#: Parameters that ``graph.get_context`` (the ``auto`` scope in v4) accepts.
_AUTO_SUPPORTED_PARAMS = frozenset({"query", "filters"})


async def _run_search(client: AsyncZep, graph_uuid: str, search_kwargs: dict[str, Any]) -> str:
    """Run one v4 graph search and format its result.

    The ``scope`` selects the v4 method. Every scope except ``auto`` calls a
    scope-specific search method and reads the items of the first page. The
    ``auto`` scope calls ``graph.get_context``, which returns a prompt-ready
    context string and accepts neither a reranker nor a limit.
    """
    kwargs = dict(search_kwargs)
    scope = str(kwargs.pop("scope", "edges"))

    if scope == "auto":
        dropped = [name for name in kwargs if name not in _AUTO_SUPPORTED_PARAMS]
        for name in dropped:
            del kwargs[name]
        if dropped:
            logger.debug("scope='auto' ignores the parameters %s", sorted(dropped))
        response = await client.graph.get_context(graph_uuid, **kwargs)
        context = response.context
        if context and str(context).strip():
            return str(context).strip()
        return "No results found."

    if scope == "edges":
        edge_pager = await client.graph.search_edges(graph_uuid, **kwargs)
        return _format_graph_results(list(edge_pager.items or []), scope)
    if scope == "nodes":
        node_pager = await client.graph.search_nodes(graph_uuid, **kwargs)
        return _format_graph_results(list(node_pager.items or []), scope)
    if scope == "episodes":
        episode_pager = await client.graph.search_episodes(graph_uuid, **kwargs)
        return _format_graph_results(list(episode_pager.items or []), scope)
    if scope == "observations":
        observation_pager = await client.graph.search_observations(graph_uuid, **kwargs)
        return _format_graph_results(list(observation_pager.items or []), scope)
    if scope == "thread_summaries":
        summary_pager = await client.graph.search_thread_summaries(graph_uuid, **kwargs)
        return _format_graph_results(list(summary_pager.items or []), scope)

    return f"Error: unknown search scope {scope!r}."


def create_search_memory_tool(
    client: AsyncZep,
    graph_uuid: str,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    # Back-compat: the original constructor args.  Each, if passed, pins
    # (hides) the corresponding parameter -- equivalent to putting it in
    # ``pinned_params``.
    scope: str | None = None,
    limit: int | None = None,
) -> Any:
    """
    Create a tool function for searching Zep conversation memory.

    The returned function searches the graph of the user for relevant
    memories. Compatible with AG2's @register_for_llm / @register_for_execution
    decorators.

    **Pin-or-expose.** Every search parameter (``scope``, ``reranker``,
    ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model
    in the tool's schema by default. Use ``pinned_params`` to fix a parameter
    to a constant value and remove it from the schema; use ``hidden_params``
    to remove a parameter from the schema *without* pinning it -- Zep's own
    server-side default applies. See :func:`create_search_graph_tool` for the
    full parameter reference; the two factories share the same pin-or-expose
    contract.

    Args:
        client: An initialized AsyncZep instance (reused for all calls).
        graph_uuid: The UUID of the graph of the user. Read it from
            ``user.graph_uuid`` one time and store it in your own database.
        pinned_params: Optional mapping of a search parameter name to a fixed
            value. Pinned parameters are hidden from the model's tool schema
            and always sent with the given value.
        hidden_params: Optional set of search parameter names to hide from
            the model's tool schema without pinning them.
        filters: Optional Zep search filters (constructor-only).
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        A callable suitable for AG2 tool registration.

    Raises:
        ZepAG2MemoryError: If ``graph_uuid`` is empty.
        ValueError: If ``pinned_params``/``hidden_params`` (or a legacy alias)
            contains an unknown parameter name.
    """
    if not graph_uuid:
        raise ZepAG2MemoryError("graph_uuid is required")

    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params, hidden_params=hidden_params, scope=scope, limit=limit
    )

    exposed = {
        name: spec
        for name, spec in _SEARCH_PARAM_SPECS.items()
        if name not in pinned and name not in hidden
    }
    signature, annotations = _build_search_signature(exposed)

    constructor_only: dict[str, Any] = {}
    if filters is not None:
        constructor_only["filters"] = filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    async def _search(**kwargs: Any) -> str:
        search_kwargs = _build_search_kwargs(
            kwargs, pinned=pinned, hidden=hidden, constructor_only=constructor_only
        )
        if not search_kwargs.get("query"):
            return "Error: No search query provided."
        try:
            return await _run_search(client, graph_uuid, search_kwargs)
        except Exception as e:
            logger.error("Zep search_memory failed: %s", type(e).__name__)
            return "Error: unable to search memory at this time."

    def search_memory(*args: Any, **kwargs: Any) -> str:
        """Search Zep memory for relevant information based on a query."""
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return str(_run_sync(_search(**dict(bound.arguments))))

    search_memory.__signature__ = signature  # type: ignore[attr-defined]
    search_memory.__annotations__ = {**annotations, "return": str}
    search_memory.__name__ = "search_memory"

    return search_memory


def create_add_memory_tool(
    client: AsyncZep,
    graph_uuid: str,
    thread_uuid: str | None = None,
) -> Any:
    """
    Create a tool function for adding messages to Zep conversation memory.

    The returned function stores messages in a Zep thread. If no thread_uuid is
    provided at creation time, the content is added to the graph as an episode.

    Args:
        client: An initialized AsyncZep instance (reused for all calls).
        graph_uuid: The UUID of the graph that holds the memory.
        thread_uuid: Optional UUID of the thread that stores the messages.

    Returns:
        A callable suitable for AG2 tool registration.

    Raises:
        ZepAG2MemoryError: If ``graph_uuid`` is empty.
    """
    from zep_cloud.types import AddMessage

    if not graph_uuid:
        raise ZepAG2MemoryError("graph_uuid is required")

    async def _add(content: str, role: str) -> str:
        if not thread_uuid:
            try:
                await client.graph.episode.add(
                    graph_uuid,
                    type="text",
                    data=_truncate(content, GRAPH_MAX_CHARS, "graph data"),
                )
                return "Memory added to knowledge graph successfully."
            except Exception as e:
                logger.error("Zep add_memory (graph) failed: %s", type(e).__name__)
                return "Error: unable to add memory at this time."

        try:
            message = AddMessage(
                content=_truncate(content, MESSAGE_MAX_CHARS, "message content"),
                role=_validate_role(role),
            )
            await client.thread.add_messages(
                thread_uuid,
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
    graph_uuid: str,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    # Back-compat: the original constructor args.  Each, if passed, pins
    # (hides) the corresponding parameter -- equivalent to putting it in
    # ``pinned_params``.
    scope: str | None = None,
    limit: int | None = None,
) -> Any:
    """
    Create a tool function for searching the Zep knowledge graph.

    Zep v4 addresses the graph of a user and a named graph in the same way,
    by the graph UUID. Read the UUID from ``user.graph_uuid`` or from
    ``graph.uuid_`` one time, and store it in your own database.

    **Pin-or-expose.** Every search parameter (``scope``, ``reranker``,
    ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model
    in the tool's schema by default, with the documented defaults below. Use
    ``pinned_params`` to fix a parameter to a constant value and remove it
    from the schema (the model can no longer choose it); use
    ``hidden_params`` to remove a parameter from the schema *without* pinning
    it -- Zep's own server-side default applies, and the parameter is simply
    omitted from the SDK call.

    ``filters`` and ``bfs_origin_node_uuids`` are always constructor-only:
    their complex/list-of-object shapes are not exposed to the model.

    Args:
        client: An initialized AsyncZep instance (reused for all calls).
        graph_uuid: The UUID of the graph to search.
        pinned_params: Optional mapping of a search parameter name to a fixed
            value. Pinned parameters are hidden from the model's tool schema
            and always sent with the given value.
        hidden_params: Optional set of search parameter names to hide from
            the model's tool schema without pinning them -- omitted from the
            SDK call so Zep's own default takes effect.
        filters: Optional Zep search filters (constructor-only). Supports
            ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        A callable suitable for AG2 tool registration. Calling it executes
        the v4 search method for the selected scope with
        pinned/model-provided/default parameters merged; Zep failures are
        caught and returned as an error string -- the tool never raises into
        the agent.

    Raises:
        ZepAG2MemoryError: If ``graph_uuid`` is empty.
        ValueError: If ``pinned_params``/``hidden_params`` (or a legacy alias)
            contains an unknown parameter name.
    """
    if not graph_uuid:
        raise ZepAG2MemoryError("graph_uuid is required")

    pinned, hidden = _resolve_pinned_and_hidden(
        pinned_params=pinned_params, hidden_params=hidden_params, scope=scope, limit=limit
    )

    exposed = {
        name: spec
        for name, spec in _SEARCH_PARAM_SPECS.items()
        if name not in pinned and name not in hidden
    }
    signature, annotations = _build_search_signature(exposed)

    constructor_only: dict[str, Any] = {}
    if filters is not None:
        constructor_only["filters"] = filters
    if bfs_origin_node_uuids is not None:
        constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    async def _search_g(**kwargs: Any) -> str:
        search_kwargs = _build_search_kwargs(
            kwargs, pinned=pinned, hidden=hidden, constructor_only=constructor_only
        )
        if not search_kwargs.get("query"):
            return "Error: No search query provided."
        try:
            return await _run_search(client, graph_uuid, search_kwargs)
        except Exception as e:
            logger.error("Zep search_graph failed: %s", type(e).__name__)
            return "Error: unable to search the knowledge graph at this time."

    def search_graph(*args: Any, **kwargs: Any) -> str:
        """Search the Zep knowledge graph for relevant information."""
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return str(_run_sync(_search_g(**dict(bound.arguments))))

    search_graph.__signature__ = signature  # type: ignore[attr-defined]
    search_graph.__annotations__ = {**annotations, "return": str}
    search_graph.__name__ = "search_graph"

    return search_graph


def create_add_graph_data_tool(
    client: AsyncZep,
    graph_uuid: str,
) -> Any:
    """
    Create a tool function for adding data to the Zep knowledge graph.

    Args:
        client: An initialized AsyncZep instance (reused for all calls).
        graph_uuid: The UUID of the graph that receives the data.

    Returns:
        A callable suitable for AG2 tool registration.

    Raises:
        ZepAG2MemoryError: If ``graph_uuid`` is empty.
    """
    if not graph_uuid:
        raise ZepAG2MemoryError("graph_uuid is required")

    async def _add_graph(data: str, data_type: str) -> str:
        try:
            await client.graph.episode.add(
                graph_uuid,
                type=data_type,
                data=_truncate(data, GRAPH_MAX_CHARS, "graph data"),
            )
            return f"Data added to knowledge graph {graph_uuid} successfully."
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
    graph_uuid: str,
    thread_uuid: str | None = None,
    memory_graph_uuid: str | None = None,
) -> dict[str, Any]:
    """
    Create and register all Zep tools on AG2 agents.

    This is a convenience function that creates all available tools and registers
    them using AG2's register_for_llm / register_for_execution pattern.

    Args:
        agent: The AG2 agent that will call the tools (register_for_llm).
        executor: The AG2 agent that will execute the tools (register_for_execution).
        client: An initialized AsyncZep instance (reused for all calls).
        graph_uuid: The UUID of the graph that the graph tools use.
        thread_uuid: Optional UUID of the thread for conversation memory.
        memory_graph_uuid: Optional UUID of the graph that the memory tools
            use. Defaults to ``graph_uuid``. Give the UUID of the graph of
            the user here when ``graph_uuid`` is a named graph.

    Returns:
        A dict mapping tool names to their callable functions.
    """
    tools: dict[str, Any] = {}

    memory_uuid = memory_graph_uuid or graph_uuid

    factories = [
        ("search_memory", create_search_memory_tool(client, memory_uuid)),
        ("add_memory", create_add_memory_tool(client, memory_uuid, thread_uuid)),
        ("search_graph", create_search_graph_tool(client, graph_uuid)),
        ("add_graph_data", create_add_graph_data_tool(client, graph_uuid)),
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
