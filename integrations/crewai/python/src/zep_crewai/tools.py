"""
Zep CrewAI Tools.

This module provides CrewAI tools for interacting with Zep memory storage,
including graph and user memory operations.

``ZepSearchTool``/``create_search_tool`` (BREAKING in this version -- see the
CHANGELOG) follow the pin-or-expose pattern shared by the other Zep framework
integrations: every ``graph.search`` parameter (``scope``, ``reranker``,
``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model by
default and can be pinned (fixed to a constant, hidden from the model) or
hidden (removed from the schema without pinning; Zep's own default applies)
at construction time. CrewAI's ``BaseTool`` (like LangChain's
``StructuredTool``) uses a pydantic ``args_schema`` for its tool schema, so
pin-or-expose is implemented the same way as ``zep_langgraph.tools``: the
exposed schema is built dynamically with ``pydantic.create_model`` and
assigned to the instance's ``args_schema``.

``ZepAddDataTool`` is unchanged except for output-payload truncation (see
:mod:`zep_crewai.limits`).
"""

import logging
from typing import Any, Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model
from zep_cloud.client import Zep

from .limits import truncate_graph_data

logger = logging.getLogger(__name__)

SearchScope = Literal[
    "edges",
    "nodes",
    "episodes",
    "observations",
    "thread_summaries",
    "auto",
]
SearchReranker = Literal["rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"]

#: Zep caps ``graph.search`` ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: Rerankers Zep rejects when ``scope == "auto"`` (auto always uses RRF
#: retrieval and applies its own internal cross-scope rerank).
_AUTO_INCOMPATIBLE_RERANKERS = ("node_distance", "episode_mentions")

#: Default tool name surfaced to the model.
DEFAULT_SEARCH_TOOL_NAME = "Zep Memory Search"

#: Default tool description surfaced to the model (overridden per-target in
#: :meth:`ZepSearchTool.__init__`, matching the pre-existing behavior).
DEFAULT_SEARCH_TOOL_DESCRIPTION = "Search Zep memory storage for relevant information"

# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------
# Each entry describes a graph.search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's ``graph.search()`` kwargs.  Model-
# exposed by default; hidden only when pinned or explicitly listed in
# ``hidden_params``.  ``annotation`` is the typed annotation used to build the
# dynamic pydantic args_schema.

_SEARCH_PARAM_SPECS: dict[str, dict[str, Any]] = {
    "scope": {
        "annotation": SearchScope,
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
        "annotation": SearchReranker,
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
_CONSTRUCTOR_ONLY_PARAMS = frozenset({"search_filters", "bfs_origin_node_uuids"})

#: All parameters that may be pinned or hidden at construction.
_PINNABLE_PARAMS = frozenset(_SEARCH_PARAM_SPECS.keys())


def _build_args_schema(*, pinned: dict[str, Any], hidden: set[str]) -> type[BaseModel]:
    """Build the pydantic model used as the tool's ``args_schema``.

    ``query`` is always present and required. Params in ``_SEARCH_PARAM_SPECS``
    that are neither pinned nor hidden become model-visible fields with their
    documented default.
    """
    fields: dict[str, Any] = {
        "query": (str, Field(description="The search query to find relevant memories"))
    }
    for param_name, spec in _SEARCH_PARAM_SPECS.items():
        if param_name in pinned or param_name in hidden:
            continue  # pinned or hidden -> not exposed to the model
        fields[param_name] = (
            spec["annotation"],
            Field(default=spec["default"], description=spec["description"]),
        )
    return create_model("ZepSearchInput", **fields)


def _resolve_pinned_and_hidden(
    *,
    pinned_params: dict[str, Any] | None,
    hidden_params: set[str] | None,
    scope: SearchScope | None,
    reranker: SearchReranker | None,
    limit: int | None,
) -> tuple[dict[str, Any], set[str]]:
    """Merge explicit pin/hide args with legacy back-compat constructor args."""
    pinned: dict[str, Any] = dict(pinned_params or {})
    hidden: set[str] = set(hidden_params or ())

    # Legacy constructor args pin (and thus hide) their parameter, same as
    # passing it via pinned_params -- back-compat for the pre-pin-or-expose API.
    if scope is not None:
        pinned.setdefault("scope", scope)
    if reranker is not None:
        pinned.setdefault("reranker", reranker)
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
                "ZepSearchTool limit %d exceeds Zep ceiling %d; clamping to %d",
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
                "ZepSearchTool reranker %r is invalid for scope='auto'; "
                "omitting reranker (auto search uses RRF).",
                pinned["reranker"],
            )
        del pinned["reranker"]

    return pinned, hidden


def _build_search_kwargs(
    call_args: dict[str, Any],
    *,
    pinned: dict[str, Any],
    hidden: set[str],
    target: dict[str, str],
    constructor_only: dict[str, Any],
) -> dict[str, Any]:
    """Merge pinned / model-provided / default parameters for one search call.

    A param pinned or hidden is never read from ``call_args``. A param that is
    neither pinned nor supplied by the model is omitted entirely -- in
    particular, ``mmr_lambda``/``center_node_uuid`` (whose spec default is
    ``None``) are never forwarded as an explicit ``None``, matching the
    sibling ports' ``if value is not None`` guard so Zep's own server-side
    default applies instead of an explicit null on the wire.
    """
    query = str(call_args.get("query", ""))[:400]
    search_kwargs: dict[str, Any] = {"query": query, **target}

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
    # 400s (a pinned limit was already clamped at construction).
    if "limit" in search_kwargs:
        limit_value = search_kwargs["limit"]
        if limit_value > MAX_SEARCH_LIMIT:
            logger.warning(
                "ZepSearchTool limit %d exceeds Zep ceiling %d; clamping to %d",
                limit_value,
                MAX_SEARCH_LIMIT,
                MAX_SEARCH_LIMIT,
            )
            search_kwargs["limit"] = MAX_SEARCH_LIMIT
        elif limit_value < 1:
            search_kwargs["limit"] = 1

    effective_scope = search_kwargs.get("scope", "edges")
    if effective_scope == "auto" and "reranker" in search_kwargs:
        # Auto search always uses RRF internally and ignores reranker
        # entirely; Zep rejects node_distance/episode_mentions outright.
        # Warn only when the (would-be) reranker is one Zep would reject.
        dropped_reranker = search_kwargs.pop("reranker")
        if dropped_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
            logger.warning(
                "ZepSearchTool reranker %r is invalid for scope='auto'; omitting reranker.",
                dropped_reranker,
            )

    search_kwargs.update(constructor_only)
    return search_kwargs


def _format_results(result: Any, scope: str) -> str:
    """Format ``GraphSearchResults`` into text for the model."""
    if scope == "auto":
        context: str | None = getattr(result, "context", None)
        if context and context.strip():
            return context.strip()

    parts: list[str] = []
    if scope == "edges":
        for edge in getattr(result, "edges", None) or []:
            fact = getattr(edge, "fact", None)
            if fact:
                parts.append(f"- {fact}")
    elif scope == "nodes":
        for node in getattr(result, "nodes", None) or []:
            name = getattr(node, "name", None) or "Entity"
            summary = getattr(node, "summary", None)
            parts.append(f"- {name}: {summary}" if summary else f"- {name}")
    elif scope == "episodes":
        for episode in getattr(result, "episodes", None) or []:
            content = getattr(episode, "content", None)
            if content:
                parts.append(f"- {content}")
    elif scope == "observations":
        for observation in getattr(result, "observations", None) or []:
            name = getattr(observation, "name", None) or "Observation"
            summary = getattr(observation, "summary", None)
            parts.append(f"- {name}: {summary}" if summary else f"- {name}")
    elif scope == "thread_summaries":
        for thread_summary in getattr(result, "thread_summaries", None) or []:
            summary = getattr(thread_summary, "summary", None)
            text = summary or getattr(thread_summary, "name", None)
            if text:
                parts.append(f"- {text}")

    if parts:
        return "\n".join(parts)
    return "No results found."


class AddGraphDataInput(BaseModel):
    """Input schema for adding data to graph."""

    data: str = Field(..., description="The data/information to store in the graph")
    data_type: str = Field(default="text", description="Type of data: 'text', 'json', or 'message'")


class ZepSearchTool(BaseTool):
    """
    Tool for searching Zep memory storage.

    Can search either graph memory or user memory depending on initialization.

    **Pin-or-expose.** Every ``graph.search`` parameter (``scope``,
    ``reranker``, ``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed
    to the model in the tool's ``args_schema`` by default, with the
    documented defaults in :data:`_SEARCH_PARAM_SPECS`. Use ``pinned_params``
    to fix a parameter to a constant value and remove it from the schema (the
    model can no longer choose it); use ``hidden_params`` to remove a
    parameter from the schema *without* pinning it -- Zep's own server-side
    default applies, and the parameter is simply omitted from the SDK call.

    ``search_filters`` and ``bfs_origin_node_uuids`` are always
    constructor-only: their complex/list-of-object shapes are not exposed to
    the model.
    """

    name: str = DEFAULT_SEARCH_TOOL_NAME
    description: str = DEFAULT_SEARCH_TOOL_DESCRIPTION

    def __init__(
        self,
        client: Zep,
        graph_id: str | None = None,
        user_id: str | None = None,
        *,
        pinned_params: dict[str, Any] | None = None,
        hidden_params: set[str] | None = None,
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
        # Back-compat: the original constructor args.  Each, if passed, pins
        # (hides) the corresponding parameter -- equivalent to putting it in
        # ``pinned_params``.
        scope: SearchScope | None = None,
        reranker: SearchReranker | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ):
        """
        Initialize search tool bound to either a graph or user.

        Args:
            client: Zep client instance
            graph_id: Graph ID for generic knowledge graph search
            user_id: User ID for user-specific graph search
            pinned_params: Optional mapping of ``graph.search`` parameter name
                to a fixed value. Pinned parameters are hidden from the
                model's ``args_schema`` and always sent with the given value.
            hidden_params: Optional set of ``graph.search`` parameter names to
                hide from the model's ``args_schema`` without pinning them --
                omitted from the SDK call so Zep's own default takes effect.
            search_filters: Optional Zep search filters (constructor-only).
            bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
                (constructor-only).
            scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
            reranker: Deprecated back-compat alias for ``pinned_params={"reranker": reranker}``.
            limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.
            **kwargs: Additional configuration

        Raises:
            ValueError: If neither or both of ``graph_id``/``user_id`` are
                given, or ``pinned_params``/``hidden_params`` (or a legacy
                alias) contains an unknown parameter name.
        """
        if not graph_id and not user_id:
            raise ValueError("Either graph_id or user_id must be provided")

        if graph_id and user_id:
            raise ValueError("Only one of graph_id or user_id should be provided")

        pinned, hidden = _resolve_pinned_and_hidden(
            pinned_params=pinned_params,
            hidden_params=hidden_params,
            scope=scope,
            reranker=reranker,
            limit=limit,
        )

        constructor_only: dict[str, Any] = {}
        if search_filters is not None:
            constructor_only["search_filters"] = search_filters
        if bfs_origin_node_uuids is not None:
            constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        # Update description based on target
        if graph_id:
            kwargs.setdefault(
                "description", f"Search Zep graph '{graph_id}' for relevant information"
            )
        else:
            kwargs.setdefault(
                "description", f"Search user '{user_id}' memories for relevant information"
            )

        kwargs["args_schema"] = _build_args_schema(pinned=pinned, hidden=hidden)

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_id = graph_id
        self._user_id = user_id
        self._pinned = pinned
        self._hidden = hidden
        self._constructor_only = constructor_only
        # Exactly one of graph_id/user_id is set (validated above), so the
        # target value is always a str.
        target: dict[str, str]
        if graph_id:
            target = {"graph_id": graph_id}
        else:
            assert user_id is not None  # noqa: S101 - narrowing, validated above
            target = {"user_id": user_id}
        self._target = target

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_id(self) -> str | None:
        """Get the graph ID."""
        return self._graph_id

    @property
    def user_id(self) -> str | None:
        """Get the user ID."""
        return self._user_id

    def _run(self, **kwargs: Any) -> str:
        """
        Execute the search operation.

        Args:
            **kwargs: ``query`` plus any exposed (non-pinned, non-hidden)
                search parameters, as validated by ``args_schema``.

        Returns:
            Formatted search results, or an error string -- this method
            never raises.
        """
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: empty search query."

        search_kwargs = _build_search_kwargs(
            kwargs,
            pinned=self._pinned,
            hidden=self._hidden,
            target=self._target,
            constructor_only=self._constructor_only,
        )

        try:
            result = self._client.graph.search(**search_kwargs)
        except Exception as e:
            error_msg = f"Error searching Zep memory: {e}"
            logger.error(error_msg)
            return error_msg

        effective_scope = str(search_kwargs.get("scope", "edges"))
        formatted = _format_results(result, effective_scope)
        logger.info(f"Zep search for query: {query}")
        return formatted


class ZepAddDataTool(BaseTool):
    """
    Tool for adding data to Zep memory storage.

    Can add data to either graph memory or user memory depending on initialization.
    """

    name: str = "Zep Add Data"
    description: str = "Add data to Zep memory storage"
    args_schema: type[BaseModel] = AddGraphDataInput

    def __init__(
        self, client: Zep, graph_id: str | None = None, user_id: str | None = None, **kwargs: Any
    ):
        """
        Initialize add data tool bound to either a graph or user.

        Args:
            client: Zep client instance
            graph_id: Graph ID for generic knowledge graph
            user_id: User ID for user-specific graph
            **kwargs: Additional configuration
        """
        if not graph_id and not user_id:
            raise ValueError("Either graph_id or user_id must be provided")

        if graph_id and user_id:
            raise ValueError("Only one of graph_id or user_id should be provided")

        # Update description based on target
        if graph_id:
            kwargs["description"] = f"Add data to Zep graph '{graph_id}'"
        else:
            kwargs["description"] = f"Add data to user '{user_id}' memory"

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_id = graph_id
        self._user_id = user_id

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_id(self) -> str | None:
        """Get the graph ID."""
        return self._graph_id

    @property
    def user_id(self) -> str | None:
        """Get the user ID."""
        return self._user_id

    def _run(self, data: str, data_type: str = "text") -> str:
        """
        Execute the add data operation.

        Args:
            data: Data to store
            data_type: Type of data

        Returns:
            Success or error message
        """
        try:
            # Validate data type
            if data_type not in ["text", "json", "message"]:
                data_type = "text"

            truncated_data = truncate_graph_data(data)

            if self._graph_id:
                # Add to graph memory
                self._client.graph.add(graph_id=self._graph_id, type=data_type, data=truncated_data)

                success_msg = f"Successfully added {data_type} data to graph '{self._graph_id}'"
                logger.debug(f"Added data to graph {self._graph_id}: {data[:100]}...")

            else:
                # Add to user graph memory
                self._client.graph.add(user_id=self._user_id, type=data_type, data=truncated_data)

                success_msg = (
                    f"Successfully added {data_type} data to user '{self._user_id}' memory"
                )
                logger.debug(f"Added data to user {self._user_id}: {data[:100]}...")

            return success_msg

        except Exception as e:
            error_msg = f"Error adding data to Zep: {str(e)}"
            logger.error(error_msg)
            return error_msg


def create_search_tool(
    client: Zep,
    graph_id: str | None = None,
    user_id: str | None = None,
    *,
    pinned_params: dict[str, Any] | None = None,
    hidden_params: set[str] | None = None,
    search_filters: dict[str, Any] | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    scope: SearchScope | None = None,
    reranker: SearchReranker | None = None,
    limit: int | None = None,
) -> ZepSearchTool:
    """
    Create a search tool bound to a Zep client.

    See :class:`ZepSearchTool` for the full pin-or-expose parameter contract.

    Args:
        client: Zep client instance
        graph_id: Optional graph ID for generic knowledge graph
        user_id: Optional user ID for user-specific graph
        pinned_params: Optional mapping of ``graph.search`` parameter name to
            a fixed value (hidden from the model, always sent).
        hidden_params: Optional set of parameter names to hide from the model
            without pinning (omitted from the SDK call).
        search_filters: Optional Zep search filters (constructor-only).
        bfs_origin_node_uuids: Optional list of node UUIDs for BFS seeding
            (constructor-only).
        scope: Deprecated back-compat alias for ``pinned_params={"scope": scope}``.
        reranker: Deprecated back-compat alias for ``pinned_params={"reranker": reranker}``.
        limit: Deprecated back-compat alias for ``pinned_params={"limit": limit}``.

    Returns:
        ZepSearchTool instance

    Raises:
        ValueError: If neither or both IDs are provided, or an unknown
            pinned/hidden parameter is given.
    """
    return ZepSearchTool(
        client=client,
        graph_id=graph_id,
        user_id=user_id,
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        search_filters=search_filters,
        bfs_origin_node_uuids=bfs_origin_node_uuids,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )


def create_add_data_tool(
    client: Zep, graph_id: str | None = None, user_id: str | None = None
) -> ZepAddDataTool:
    """
    Create an add data tool bound to a Zep client.

    Args:
        client: Zep client instance
        graph_id: Optional graph ID for generic knowledge graph
        user_id: Optional user ID for user-specific graph

    Returns:
        ZepAddDataTool instance

    Raises:
        ValueError: If neither or both IDs are provided
    """
    return ZepAddDataTool(client=client, graph_id=graph_id, user_id=user_id)
