"""
ZepGraphSearchTool -- a model-callable ADK tool for searching Zep knowledge graphs.

Unlike ``ZepContextTool`` (which injects context automatically on every turn),
this tool is visible to the model in the tool list and called on-demand when
the model decides it needs to search the knowledge graph.

Search parameters can be *pinned* at construction time -- pinned parameters are
removed from the schema the model sees, locking them to a fixed value.  Any
parameter not pinned is exposed to the model with a reasonable default.

The tool resolves the search target automatically:

* If ``graph_id`` is set at construction → searches that shared graph for all
  users (e.g. a documentation knowledge base).
* If ``graph_id`` is not set → resolves ``user_id`` from ADK session state at
  runtime and searches the current user's personal graph.
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
# Each entry describes a graph.search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's ``graph.search()`` kwargs.

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
            "'nodes' for entity summaries, 'episodes' for raw messages."
        ),
        "enum": ["edges", "nodes", "episodes"],
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


class ZepGraphSearchTool(BaseTool):
    """Model-callable tool for searching Zep knowledge graphs.

    This tool is added to the model's tool list and called on-demand when the
    model decides it needs to search for facts, entities, or prior messages.

    Any search parameter can be *pinned* at construction time by passing it as
    a keyword argument.  Pinned parameters are hidden from the model and locked
    to the given value.  Parameters not pinned are exposed in the model's tool
    schema with sensible defaults.

    Args:
        zep_client: An initialised ``AsyncZep`` client.
        graph_id: Optional fixed graph ID for shared-graph search.  When set,
            all searches target this graph regardless of which user's session
            is active.  When ``None``, the tool searches the current user's
            personal graph (resolved from session state).
        name: Tool name visible to the model.
        description: Tool description visible to the model.
        search_filters: Optional Zep search filters (constructor-only).
            Supports ``node_labels``, ``edge_types``, ``exclude_node_labels``,
            ``exclude_edge_types``, and property filters.
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        **pinned: Any graph.search parameter to fix at construction time.
            Supported: ``scope``, ``reranker``, ``limit``, ``mmr_lambda``,
            ``center_node_uuid``.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        graph_id: str | None = None,
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
        self._graph_id: str | None = graph_id

        # Validate pinned params
        if "user_id" in pinned:
            raise ValueError(
                "'user_id' cannot be pinned. Per-user graph search is resolved "
                "from session state at runtime. Use 'graph_id' for shared "
                "graph search."
            )
        allowed_pinned = frozenset(_SEARCH_PARAMS.keys())
        unknown = set(pinned.keys()) - allowed_pinned
        if unknown:
            raise ValueError(
                f"Unknown pinned parameters: {unknown}. Allowed: {sorted(allowed_pinned)}"
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

        if self._graph_id:
            search_kwargs["graph_id"] = self._graph_id
        else:
            user_id = self._resolve_user_id(tool_context)
            if not user_id:
                return "Error: Cannot determine user ID from session state."
            search_kwargs["user_id"] = user_id

        # --- Merge params: pinned > model-provided > default ----------
        for param_name, param_def in _SEARCH_PARAMS.items():
            if param_name in self._pinned:
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
        scope = search_kwargs.get("scope", "edges")

        try:
            result = await self._zep.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning("Zep graph search failed: %s", exc, exc_info=True)
            return f"Graph search failed: {exc}"

        return self._format_results(result, scope)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_user_id(tool_context: ToolContext) -> str | None:
        """Resolve user_id from session state or ADK session metadata."""
        state = tool_context.state
        if state is not None:
            user_id = state.get("zep_user_id")
            if user_id:
                return str(user_id)
        try:
            return tool_context._invocation_context.session.user_id  # type: ignore[union-attr]
        except AttributeError:
            return None

    @staticmethod
    def _format_results(result: Any, scope: str) -> str:
        """Format search results as readable text for the model."""
        parts: list[str] = []

        if scope == "edges" and result.edges:
            for edge in result.edges:
                if edge.fact:
                    parts.append(f"- {edge.fact}")
        elif scope == "nodes" and result.nodes:
            for node in result.nodes:
                name = getattr(node, "name", None) or "Entity"
                summary = getattr(node, "summary", None)
                if summary:
                    parts.append(f"- {name}: {summary}")
        elif scope == "episodes" and result.episodes:
            for ep in result.episodes:
                content = getattr(ep, "content", None)
                if content:
                    parts.append(f"- {content}")

        if not parts:
            return "No results found."

        return "\n".join(parts)
