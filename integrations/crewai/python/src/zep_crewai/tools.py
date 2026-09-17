"""
Zep CrewAI Tools.

This module provides CrewAI tools for interacting with Zep memory storage,
including graph and user memory operations.

``ZepSearchTool``/``create_search_tool`` (BREAKING in this version -- see the
CHANGELOG) follow the pin-or-expose pattern shared by the other Zep framework
integrations: every graph search parameter (``scope``, ``reranker``,
``limit``, ``mmr_lambda``, ``center_node_uuid``) is exposed to the model by
default and can be pinned (fixed to a constant, hidden from the model) or
hidden (removed from the schema without pinning; Zep's own default applies)
at construction time. Zep v4 addresses a graph by UUID and gives one search
method for each scope, so ``scope`` selects the SDK method that the tool
calls. CrewAI's ``BaseTool`` (like LangChain's
``StructuredTool``) uses a pydantic ``args_schema`` for its tool schema, so
pin-or-expose is implemented the same way as ``zep_langgraph.tools``: the
exposed schema is built dynamically with ``pydantic.create_model`` and
assigned to the instance's ``args_schema``.

``ZepAddDataTool`` is unchanged except for output-payload truncation (see
:mod:`zep_crewai.limits`).
"""

import itertools
import logging
from typing import Any, Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, create_model
from zep_cloud.client import Zep
from zep_cloud.types import Edge, Episode, Node, Observation, ThreadSummary
from zep_cloud.types.graph_context_response import GraphContextResponse

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

#: Zep caps a search ``limit`` at 50; larger values are rejected.
MAX_SEARCH_LIMIT = 50

#: The item types that the v4 search methods return.
SearchItems = list[Edge] | list[Node] | list[Episode] | list[Observation] | list[ThreadSummary]


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
# Each entry describes a graph search parameter that can be pinned or exposed
# to the model.  Keys match the Zep SDK's search kwargs.  Model-
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


def _format_named_summary(name: str | None, summary: str | None, fallback: str) -> str:
    """Format one entity-like item as a single line."""
    label = name or fallback
    return f"- {label}: {summary}" if summary else f"- {label}"


def _format_context(response: GraphContextResponse) -> str:
    """Format the Context Block that scope ``auto`` returns."""
    context = response.context
    if context and context.strip():
        return context.strip()
    return "No results found."


def _format_items(items: SearchItems) -> str:
    """Format the items of one search scope into text for the model."""
    parts: list[str] = []
    for item in items:
        if isinstance(item, Edge):
            if item.fact:
                parts.append(f"- {item.fact}")
        elif isinstance(item, Node):
            parts.append(_format_named_summary(item.name, item.summary, "Entity"))
        elif isinstance(item, Episode):
            if item.content:
                parts.append(f"- {item.content}")
        elif isinstance(item, Observation):
            parts.append(_format_named_summary(item.name, item.summary, "Observation"))
        elif item.summary:
            parts.append(f"- {item.summary}")

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

    The tool is bound to one graph. For a user graph, pass the ``graph_uuid``
    of the user.

    **Pin-or-expose.** Every graph search parameter (``scope``,
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
        graph_uuid: str,
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
        Initialize a search tool bound to one graph.

        Args:
            client: Zep client instance
            graph_uuid: The UUID of the graph to search. For a user graph,
                pass the ``graph_uuid`` of the user.
            pinned_params: Optional mapping of a search parameter name
                to a fixed value. Pinned parameters are hidden from the
                model's ``args_schema`` and always sent with the given value.
            hidden_params: Optional set of search parameter names to
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
            ValueError: If ``graph_uuid`` is empty, or
                ``pinned_params``/``hidden_params`` (or a legacy alias)
                contains an unknown parameter name.
        """
        if not graph_uuid:
            raise ValueError("graph_uuid must be provided")

        pinned, hidden = _resolve_pinned_and_hidden(
            pinned_params=pinned_params,
            hidden_params=hidden_params,
            scope=scope,
            reranker=reranker,
            limit=limit,
        )

        constructor_only: dict[str, Any] = {}
        if search_filters is not None:
            constructor_only["filters"] = search_filters
        if bfs_origin_node_uuids is not None:
            constructor_only["bfs_origin_node_uuids"] = bfs_origin_node_uuids

        kwargs.setdefault(
            "description", f"Search Zep graph '{graph_uuid}' for relevant information"
        )

        kwargs["args_schema"] = _build_args_schema(pinned=pinned, hidden=hidden)

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_uuid = graph_uuid
        self._pinned = pinned
        self._hidden = hidden
        self._constructor_only = constructor_only

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_uuid(self) -> str:
        """Get the graph UUID."""
        return self._graph_uuid

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
            constructor_only=self._constructor_only,
        )

        effective_scope = str(search_kwargs.pop("scope", "edges"))

        try:
            if effective_scope == "auto":
                context_kwargs: dict[str, Any] = {"query": search_kwargs["query"]}
                if "filters" in search_kwargs:
                    context_kwargs["filters"] = search_kwargs["filters"]
                formatted = _format_context(
                    self._client.graph.get_context(self._graph_uuid, **context_kwargs)
                )
            else:
                formatted = _format_items(self._search(effective_scope, search_kwargs))
        except Exception as e:
            error_msg = f"Error searching Zep memory: {e}"
            logger.error(error_msg)
            return error_msg

        logger.info(f"Zep search for query: {query}")
        return formatted

    def _search(self, scope: str, search_kwargs: dict[str, Any]) -> SearchItems:
        """Call the v4 search method for ``scope`` and read its pager.

        v4 gives one search method for each scope, and each method returns a
        pager. The tool reads up to ``limit`` items from that pager.
        """
        graph = self._client.graph
        limit = int(search_kwargs.get("limit", MAX_SEARCH_LIMIT))

        if scope == "nodes":
            return list(
                itertools.islice(graph.search_nodes(self._graph_uuid, **search_kwargs), limit)
            )
        if scope == "episodes":
            return list(
                itertools.islice(graph.search_episodes(self._graph_uuid, **search_kwargs), limit)
            )
        if scope == "observations":
            return list(
                itertools.islice(
                    graph.search_observations(self._graph_uuid, **search_kwargs), limit
                )
            )
        if scope == "thread_summaries":
            return list(
                itertools.islice(
                    graph.search_thread_summaries(self._graph_uuid, **search_kwargs), limit
                )
            )
        return list(itertools.islice(graph.search_edges(self._graph_uuid, **search_kwargs), limit))


class ZepAddDataTool(BaseTool):
    """
    Tool for adding data to Zep memory storage.

    The tool is bound to one graph. For a user graph, pass the ``graph_uuid``
    of the user.
    """

    name: str = "Zep Add Data"
    description: str = "Add data to Zep memory storage"
    args_schema: type[BaseModel] = AddGraphDataInput

    def __init__(self, client: Zep, graph_uuid: str, **kwargs: Any):
        """
        Initialize an add data tool bound to one graph.

        Args:
            client: Zep client instance
            graph_uuid: The UUID of the graph. For a user graph, pass the
                ``graph_uuid`` of the user.
            **kwargs: Additional configuration
        """
        if not graph_uuid:
            raise ValueError("graph_uuid must be provided")

        kwargs["description"] = f"Add data to Zep graph '{graph_uuid}'"

        super().__init__(**kwargs)

        # Store as private attributes to avoid Pydantic validation
        self._client = client
        self._graph_uuid = graph_uuid

    @property
    def client(self) -> Zep:
        """Get the Zep client."""
        return self._client

    @property
    def graph_uuid(self) -> str:
        """Get the graph UUID."""
        return self._graph_uuid

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

            self._client.graph.episode.add(self._graph_uuid, type=data_type, data=truncated_data)

            success_msg = f"Successfully added {data_type} data to graph '{self._graph_uuid}'"
            logger.debug(f"Added data to graph {self._graph_uuid}: {data[:100]}...")

            return success_msg

        except Exception as e:
            error_msg = f"Error adding data to Zep: {str(e)}"
            logger.error(error_msg)
            return error_msg


def create_search_tool(
    client: Zep,
    graph_uuid: str,
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
        graph_uuid: The UUID of the graph to search. For a user graph, pass
            the ``graph_uuid`` of the user.
        pinned_params: Optional mapping of a search parameter name to
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
        ValueError: If ``graph_uuid`` is empty, or an unknown pinned/hidden
            parameter is given.
    """
    return ZepSearchTool(
        client=client,
        graph_uuid=graph_uuid,
        pinned_params=pinned_params,
        hidden_params=hidden_params,
        search_filters=search_filters,
        bfs_origin_node_uuids=bfs_origin_node_uuids,
        scope=scope,
        reranker=reranker,
        limit=limit,
    )


def create_add_data_tool(client: Zep, graph_uuid: str) -> ZepAddDataTool:
    """
    Create an add data tool bound to a Zep client.

    Args:
        client: Zep client instance
        graph_uuid: The UUID of the graph. For a user graph, pass the
            ``graph_uuid`` of the user.

    Returns:
        ZepAddDataTool instance

    Raises:
        ValueError: If ``graph_uuid`` is empty
    """
    return ZepAddDataTool(client=client, graph_uuid=graph_uuid)
