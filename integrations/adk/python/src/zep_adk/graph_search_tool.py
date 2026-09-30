"""
ZepGraphSearchTool -- a model-callable ADK tool for searching Zep knowledge graphs.

Unlike ``ZepContextTool`` (which injects context automatically on every turn),
this tool is visible to the model in the tool list and called on-demand when
the model decides it needs to search the knowledge graph.

Search parameters can be *pinned* at construction time -- pinned parameters are
removed from the schema the model sees, locking them to a fixed value.  Any
parameter not pinned is exposed to the model with a reasonable default.

Zep v4 addresses every graph by a server-generated UUID, and it gives each
scope its own SDK method (``graph.search_edges``, ``graph.search_nodes``, and
so on).  The ``auto`` scope maps to ``graph.get_context``.

The tool resolves the search target automatically:

* If ``graph_uuid`` is set at construction -> searches that shared graph for
  all users (e.g. a documentation knowledge base).
* If ``graph_uuid`` is not set -> resolves the user's graph UUID from ADK
  session state at runtime and searches the current user's personal graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from typing_extensions import override
from zep_cloud.client import AsyncZep

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a search parameter that can be pinned or exposed to the
# model.  Keys match the Zep SDK's ``graph.search_*()`` kwargs, except for
# ``scope``, which selects the SDK method.

_SEARCH_PARAMS: dict[str, dict[str, Any]] = {
    "query": {
        "type": "STRING",
        "description": "Search query text (max 400 characters).",
        "required": True,
    },
    "scope": {
        "type": "STRING",
        "description": (
            "What to search for: 'edges' for facts and relationships, "
            "'nodes' for entities and their summaries, "
            "'episodes' for raw text data (unstructured text, messages, or JSON), "
            "'observations' for derived memories, "
            "'thread_summaries' for incremental thread summaries, "
            "'auto' to let Zep decide the best mix of results."
        ),
        "enum": ["edges", "nodes", "episodes", "observations", "thread_summaries", "auto"],
        "default": "edges",
    },
    "reranker": {
        "type": "STRING",
        "description": (
            "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), "
            "'cross_encoder' (highest accuracy), 'episode_mentions' "
            "(frequently referenced), 'node_distance' (near a specific entity)."
        ),
        "enum": ["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"],
        "default": "rrf",
    },
    "limit": {
        "type": "INTEGER",
        "description": "Maximum number of results to return.",
        "default": 10,
    },
    "mmr_lambda": {
        "type": "NUMBER",
        "description": (
            "Balance between diversity (0.0) and relevance (1.0). Only used when reranker is 'mmr'."
        ),
    },
    "center_node_uuid": {
        "type": "STRING",
        "description": (
            "UUID of the center node for distance-based reranking. "
            "Required when reranker is 'node_distance'."
        ),
    },
}

#: Maps a search scope to the Zep v4 SDK method that implements it.  The
#: ``auto`` scope is not here: it maps to ``graph.get_context``, which returns
#: a pre-materialized Context Block instead of a page of items.
_SCOPE_METHODS: dict[str, str] = {
    "edges": "search_edges",
    "nodes": "search_nodes",
    "episodes": "search_episodes",
    "observations": "search_observations",
    "thread_summaries": "search_thread_summaries",
}

#: The scope that maps to ``graph.get_context`` instead of a search method.
AUTO_SCOPE = "auto"

#: Every scope that :func:`search_graph` accepts.
SUPPORTED_SCOPES = frozenset(_SCOPE_METHODS) | {AUTO_SCOPE}

_TYPE_MAP: dict[str, types.Type] = {
    "STRING": types.Type.STRING,
    "INTEGER": types.Type.INTEGER,
    "NUMBER": types.Type.NUMBER,
    "BOOLEAN": types.Type.BOOLEAN,
}

# Parameters that are always constructor-only (complex types not suitable for
# model schema generation).
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

# All parameters that may be pinned at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAMS.keys()) | _CONSTRUCTOR_ONLY_PARAMS


def _name_summary_text(name: str | None, summary: str | None) -> str:
    """Join a name and summary as "name: summary".

    Falls back to whichever half is present, and "" when both are absent.
    Mirrors the Go integration's ``nameSummaryText`` helper so nodes,
    observations, and thread summaries render identically across languages.
    """
    if name and summary:
        return f"{name}: {summary}"
    if name:
        return name
    if summary:
        return summary
    return ""


def scope_results_to_texts(items: Any, scope: str) -> list[str]:
    """Flatten one page of search results into plain-text items for one scope.

    ``items`` is the ``items`` list of a Zep v4 pager (``AsyncPager.items``).
    Shared by :class:`ZepGraphSearchTool` (which prefixes each item with
    ``"- "`` for the model) and :class:`~zep_adk.memory_service.ZepMemoryService`
    (which wraps each item in its own ``MemoryEntry``). The ``auto`` scope is
    intentionally excluded: ``graph.get_context`` returns a single
    pre-materialized Context Block rather than a list of discrete items, so
    callers handle it separately.

    For ``nodes``, ``observations``, and ``thread_summaries``, an item with a
    name but no summary still yields a result (just the name) rather than
    being dropped -- the same full name/summary fallback used by the Go and
    TypeScript integrations.
    """
    texts: list[str] = []

    for item in items or []:
        if scope == "edges":
            fact = getattr(item, "fact", None)
            if fact:
                texts.append(fact)
        elif scope == "episodes":
            content = getattr(item, "content", None)
            if content:
                texts.append(content)
        elif scope in ("nodes", "observations", "thread_summaries"):
            text = _name_summary_text(getattr(item, "name", None), getattr(item, "summary", None))
            if text:
                texts.append(text)

    return texts


async def search_graph(
    zep: AsyncZep,
    *,
    graph_uuid: str,
    scope: str,
    query: str,
    limit: int | None = None,
    reranker: str | None = None,
    mmr_lambda: float | None = None,
    center_node_uuid: str | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
) -> list[str]:
    """Search one Zep graph and return the results as plain-text items.

    Dispatches to the Zep v4 SDK method for the scope: ``graph.search_edges``,
    ``graph.search_nodes``, ``graph.search_episodes``,
    ``graph.search_observations``, ``graph.search_thread_summaries``, or
    ``graph.get_context`` for ``auto``.  The search methods return a pager;
    this helper reads the first page, which holds up to ``limit`` items.

    Args:
        zep: An initialised ``AsyncZep`` client.
        graph_uuid: The UUID of the graph to search.
        scope: One of :data:`SUPPORTED_SCOPES`.
        query: The search query text.
        limit: An optional maximum number of results.
        reranker: An optional result ordering algorithm.
        mmr_lambda: An optional MMR lambda, used when ``reranker`` is ``mmr``.
        center_node_uuid: An optional center node, used when ``reranker`` is
            ``node_distance``.
        search_filters: Optional Zep search filters, sent as the SDK's
            ``filters`` argument.
        bfs_origin_node_uuids: An optional list of node UUIDs that seed a BFS
            traversal.

    Returns:
        A list of plain-text results.  The ``auto`` scope returns at most one
        item, the Context Block.

    Raises:
        ValueError: If ``scope`` is not supported.
    """
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(f"Unsupported search scope: {scope!r}")

    kwargs: dict[str, Any] = {"query": query}
    if search_filters is not None:
        kwargs["filters"] = search_filters

    if scope == AUTO_SCOPE:
        response = await zep.graph.get_context(graph_uuid, **kwargs)
        context = getattr(response, "context", None)
        return [context.strip()] if context and context.strip() else []

    if limit is not None:
        kwargs["limit"] = limit
    if reranker is not None:
        kwargs["reranker"] = reranker
    if mmr_lambda is not None:
        kwargs["mmr_lambda"] = mmr_lambda
    if center_node_uuid is not None:
        kwargs["center_node_uuid"] = center_node_uuid
    if bfs_origin_node_uuids is not None:
        kwargs["bfs_origin_node_uuids"] = bfs_origin_node_uuids

    method = getattr(zep.graph, _SCOPE_METHODS[scope])
    pager = await method(graph_uuid, **kwargs)
    return scope_results_to_texts(getattr(pager, "items", None), scope)


async def resolve_user_graph_uuid(zep: AsyncZep, user_uuid: str) -> str | None:
    """Return the UUID of the graph that belongs to a Zep user.

    The call addresses the user by UUID; it is not a name lookup.  Store
    ``user.graph_uuid`` at provisioning time and put it in ADK session state as
    ``zep_graph_uuid`` to avoid this call.
    """
    user = await zep.user.get(user_uuid)
    graph_uuid = getattr(user, "graph_uuid", None)
    return str(graph_uuid) if graph_uuid else None


class ZepGraphSearchTool(BaseTool):
    """Model-callable tool for searching Zep knowledge graphs.

    This tool is added to the model's tool list and called on-demand when the
    model decides it needs to search for facts, entities, or prior messages.

    Any search parameter can be *pinned* at construction time by passing it as
    a keyword argument.  Pinned parameters are hidden from the model and locked
    to the given value.  Parameters not pinned are exposed in the model's tool
    schema with sensible defaults.

    Pinning an optional parameter to ``None`` hides it from the model schema
    without passing it to the Zep SDK -- useful for suppressing irrelevant
    parameters (e.g. ``mmr_lambda=None`` when the reranker is not ``mmr``).

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        graph_uuid: Optional fixed graph UUID for shared-graph search.  When
            set, all searches target this graph regardless of which user's
            session is active.  When ``None``, the tool searches the current
            user's personal graph (resolved from session state).
        name: Tool name visible to the model.
        description: Tool description visible to the model.
        search_filters: Optional Zep search filters (constructor-only).
            Supports ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        **pinned: Any search parameter to fix at construction time.
            Supported: ``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
            ``center_node_uuid``.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_uuid: str | None = None,
        name: str = "zep_graph_search",
        description: str = (
            "Search the user's knowledge graph for information from previous "
            "conversations, known facts about the user, or general context. "
            "Use this to look up specific details the user has shared before."
        ),
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
        **pinned: Any,
    ) -> None:
        super().__init__(name=name, description=description)
        self._zep: AsyncZep = zep_client
        self._graph_uuid: str | None = graph_uuid
        # Caches user_uuid -> graph_uuid so a per-user search does not call
        # user.get on every turn.
        self._user_graph_uuids: dict[str, str] = {}

        # Validate pinned params
        if "user_uuid" in pinned:
            raise ValueError(
                "'user_uuid' cannot be pinned. Per-user graph search is resolved "
                "from session state at runtime. Use 'graph_uuid' for shared "
                "graph search."
            )
        allowed_pinned = frozenset(_SEARCH_PARAMS.keys())
        unknown = set(pinned.keys()) - allowed_pinned
        if unknown:
            raise ValueError(
                f"Unknown pinned parameters: {unknown}. Allowed: {sorted(allowed_pinned)}"
            )
        # Pinning to None means "hide from model but don't send to SDK".
        # This is only valid for optional parameters.
        for k, v in pinned.items():
            if v is None and _SEARCH_PARAMS.get(k, {}).get("required"):
                raise ValueError(
                    f"Cannot pin required parameter '{k}' to None. "
                    "Only optional parameters can be hidden with None."
                )

        # Store pinned search params
        self._pinned: dict[str, Any] = dict(pinned)
        if search_filters is not None:
            self._pinned["search_filters"] = search_filters
        if bfs_origin_node_uuids is not None:
            self._pinned["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        # Pre-build the declaration once (it's immutable after construction)
        self._declaration = self._build_declaration()

    # ------------------------------------------------------------------
    # Schema declaration
    # ------------------------------------------------------------------

    def _build_declaration(self) -> types.FunctionDeclaration:
        """Build the function declaration, excluding pinned parameters."""
        properties: dict[str, types.Schema] = {}
        required: list[str] = []

        for param_name, param_def in _SEARCH_PARAMS.items():
            if param_name in self._pinned:
                continue  # pinned → hidden from model

            schema_kwargs: dict[str, Any] = {
                "type": _TYPE_MAP[param_def["type"]],
                "description": param_def.get("description", ""),
            }
            if "enum" in param_def:
                schema_kwargs["enum"] = param_def["enum"]

            properties[param_name] = types.Schema(**schema_kwargs)

            if param_def.get("required"):
                required.append(param_name)

        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties=properties,
                required=required or None,
            ),
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration | None:
        return self._declaration

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @override
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        """Execute the graph search with merged parameters."""
        # --- Resolve search target ------------------------------------
        search_kwargs: dict[str, Any] = {}

        try:
            graph_uuid = await self._resolve_graph_uuid(tool_context)
        except Exception as exc:
            logger.warning("Cannot resolve the Zep graph UUID: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"
        if not graph_uuid:
            return "Error: Cannot determine the Zep graph UUID from session state."

        # --- Merge params: pinned > model-provided > default ----------
        # A param pinned to None is hidden from the model and omitted from
        # the SDK call (used to suppress optional params from the schema).
        for param_name, param_def in _SEARCH_PARAMS.items():
            if param_name in self._pinned:
                if self._pinned[param_name] is not None:
                    search_kwargs[param_name] = self._pinned[param_name]
            elif param_name in args:
                search_kwargs[param_name] = args[param_name]
            elif "default" in param_def:
                search_kwargs[param_name] = param_def["default"]
            # else: optional param not set by anyone → omit

        if not search_kwargs.get("query"):
            return "Error: No search query provided."

        # Constructor-only complex params
        for key in _CONSTRUCTOR_ONLY_PARAMS:
            if key in self._pinned:
                search_kwargs[key] = self._pinned[key]

        # --- Execute --------------------------------------------------
        scope = search_kwargs.pop("scope", "edges")
        if scope not in SUPPORTED_SCOPES:
            logger.warning("Unsupported Zep search scope %r; using 'edges'", scope)
            scope = "edges"

        try:
            texts = await search_graph(
                self._zep, graph_uuid=graph_uuid, scope=scope, **search_kwargs
            )
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return self._format_results(texts, scope)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_graph_uuid(self, tool_context: ToolContext) -> str | None:
        """Resolve the UUID of the graph to search.

        Resolution order: the pinned ``graph_uuid`` -> ``zep_graph_uuid`` in
        session state -> the graph of the resolved Zep user.
        """
        if self._graph_uuid:
            return self._graph_uuid

        state = tool_context.state
        if state is not None:
            graph_uuid = state.get("zep_graph_uuid")
            if graph_uuid:
                return str(graph_uuid)

        user_uuid = self._resolve_user_uuid(tool_context)
        if not user_uuid:
            return None

        cached = self._user_graph_uuids.get(user_uuid)
        if cached:
            return cached

        resolved = await resolve_user_graph_uuid(self._zep, user_uuid)
        if resolved:
            self._user_graph_uuids[user_uuid] = resolved
        return resolved

    @staticmethod
    def _resolve_user_uuid(tool_context: ToolContext) -> str | None:
        """Resolve the Zep user UUID from session state or ADK session metadata."""
        state = tool_context.state
        if state is not None:
            user_uuid = state.get("zep_user_uuid")
            if user_uuid:
                return str(user_uuid)
        try:
            return tool_context.user_id
        except AttributeError:
            return None

    @staticmethod
    def _format_results(texts: list[str], scope: str) -> str:
        """Format search results as readable text for the model."""
        if not texts:
            return "No results found."

        if scope == AUTO_SCOPE:
            # graph.get_context returns one pre-formatted Context Block.
            return texts[0]

        return "\n".join(f"- {text}" for text in texts)
