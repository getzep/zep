"""Zep ``MemoryStore`` for Strands Agents.

:class:`ZepMemoryStore` implements Strands'
:class:`~strands.memory.types.MemoryStore` Protocol so a Zep user graph (or
standalone graph) plugs into :class:`~strands.memory.MemoryManager` like any
other store:

* :meth:`search` recalls relevant knowledge via ``graph.get_context`` or the
  scoped ``graph.search_*`` methods
* :meth:`add_messages` ingests conversation turns for **server-side**
  extraction via ``thread.add_messages`` (user-graph mode)
* :meth:`add` writes a single text/JSON fact via ``graph.episode.add``
* :meth:`get_tools` optionally registers an on-demand graph-search tool

Zep v4 addresses a user, a thread, and a graph by UUID, so the store takes
``user_uuid``, ``thread_uuid``, or ``graph_uuid``. The store never creates a
resource and never resolves a name. Create the user and the thread one time
with :func:`~zep_strands.provisioning.create_user` and
:func:`~zep_strands.provisioning.create_thread`, store the UUIDs, and give
them to the store. :meth:`initialize` is deliberately inert; see its docstring
for why.

When ``extraction`` is enabled (the default in writable user/thread mode),
Strands' ``MemoryManager`` batches conversation turns and only calls
:meth:`add_messages` on its default cadence — **every 5 turns**. Until that
flush (or an explicit ``memory_manager.flush()``), nothing has been sent to
Zep, so the graph builds later than turn-by-turn persistence would. After
messages do reach Zep, ingestion is still asynchronous.

Failure-handling contract
-------------------------

**Zep SDK errors propagate out of the store methods on purpose. Do not wrap
them in ``try``/``except`` that returns an empty or ``None`` fallback** — in
Strands the *framework* owns failure isolation, and swallowing breaks it:

* :meth:`search` — ``MemoryManager.search`` gathers stores with
  ``return_exceptions=True``, logs a failing store and skips it; context
  injection additionally fails open. A Zep outage already degrades the turn to
  a memoryless one without crashing the agent.
* :meth:`add` — ``MemoryManager.add`` collects per-store failures into an
  ``AggregateMemoryError`` specifically so a failed write is never silent.
* :meth:`add_messages` — ``ExtractionCoordinator`` catches store exceptions
  ("saving must never break the agent loop") and **rolls its per-store
  high-water mark back so the batch retries**. Returning ``None`` after
  swallowing an error would be read as success, advancing the mark and
  discarding that batch permanently.

This mirrors the SDK's own vended stores (``TestMemoryStore``,
``BedrockKnowledgeBaseStore``), which raise rather than degrade. The one place
this integration *does* swallow is the model-callable ``zep_search`` tool,
which returns an error string — a raw tool has no framework layer above it.

Attach it through a ``MemoryManager``::

    from strands import Agent
    from strands.memory import MemoryManager
    from zep_cloud.client import AsyncZep
    from zep_strands import ZepMemoryStore

    zep = AsyncZep(api_key="...")
    store = ZepMemoryStore(
        zep_client=zep,
        user_uuid=user_uuid,
        thread_uuid=thread_uuid,
        first_name="Jane",
        last_name="Smith",
        writable=True,
        extraction=True,  # server-side via add_messages; default cadence = every 5 turns
    )
    agent = Agent(memory_manager=MemoryManager(stores=[store]))
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from strands.memory.types import (
    AddMessagesContext,
    MemoryEntry,
    Metadata,
    SearchOptions,
)
from strands.types.content import Message
from strands.types.tools import AgentTool
from zep_cloud import AddMessage
from zep_cloud.client import AsyncZep

from ._graph import GraphUuidResolver
from ._text import (
    GRAPH_DATA_TRUNCATE_LIMIT,
    truncate_graph_data,
    truncate_message_content,
)
from .search import (
    _AUTO_INCOMPATIBLE_RERANKERS,
    MAX_SEARCH_LIMIT,
    Scope,
    SearchResults,
    create_zep_search_tool,
    result_text,
    run_graph_search,
)

logger = logging.getLogger(__name__)

DEFAULT_STORE_NAME = "zep"
DEFAULT_STORE_DESCRIPTION = (
    "Long-term memory backed by Zep's temporal Context Graph — facts, "
    "entities, and prior conversations about the user."
)
DEFAULT_MAX_SEARCH_RESULTS = 10

#: Metadata key that selects the ``graph.episode.add`` data type for :meth:`ZepMemoryStore.add`.
#: Values: ``"text"`` (default), ``"json"``, or ``"message"``.
ADD_TYPE_METADATA_KEY = "type"

GraphAddType = Literal["text", "json", "message"]


def _extraction_enabled(extraction: Any) -> bool:
    """Return whether ``extraction`` enables automatic extraction.

    ``None`` / ``False`` are off; ``True`` or an ``ExtractionConfig`` are on.
    """
    return extraction is not None and extraction is not False


def _require_extraction_support(
    *,
    writable: bool,
    user_uuid: str | None,
    thread_uuid: str | None,
) -> None:
    """Raise if extraction is enabled without a writable user/thread store.

    Server-side extraction is implemented via :meth:`ZepMemoryStore.add_messages`,
    which requires a Zep user thread. Fail at construction so ``MemoryManager``
    never schedules extraction that would raise on every cycle.
    """
    if not writable:
        raise ValueError(
            "ZepMemoryStore: extraction requires writable=True "
            "(server-side extraction writes via add_messages)."
        )
    if not user_uuid or not thread_uuid:
        raise ValueError(
            "ZepMemoryStore: extraction requires user-graph mode with both "
            "user_uuid and thread_uuid (server-side via add_messages). "
            "Pass extraction=False for standalone graphs or stores without a thread."
        )


def _extract_text(message: Message) -> str:
    """Join text content blocks from a Strands ``Message`` into one string."""
    parts: list[str] = []
    for block in message.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts).strip()


def _role_to_zep(role: str) -> str:
    """Map a Strands message role onto a Zep thread-message role.

    Zep v4 accepts ``user``, ``assistant``, ``system``, ``tool``, and
    ``function``. There is no ``norole``, so an unknown role becomes ``user``
    rather than the turn being dropped.
    """
    normalised = role.lower().strip()
    if normalised in {"user", "assistant", "system", "tool", "function"}:
        return normalised
    return "user"


def _results_to_entries(results: SearchResults, *, limit: int) -> list[MemoryEntry]:
    """Convert Zep search results into Strands ``MemoryEntry`` rows.

    For ``scope="auto"``, Zep's assembled Context Block becomes one entry (the
    shape Zep designs for prompt injection). A scoped search expands each
    result into its own entry, so ``MemoryManager`` can cap and format the
    results independently.
    """
    if results.scope == "auto":
        context = results.context
        if context and context.strip():
            return [MemoryEntry(content=context.strip(), metadata={"scope": "auto"})]
        return []

    entries: list[MemoryEntry] = []
    for item in results.items:
        text = result_text(item, results.scope)
        if text:
            entries.append(
                MemoryEntry(
                    content=text,
                    metadata={"scope": results.scope, "uuid": item.uuid_},
                )
            )
    return entries[:limit]


class ZepMemoryStore:
    """Strands :class:`~strands.memory.types.MemoryStore` backed by Zep.

    Two scoping modes:

    * **User graph** (``user_uuid``, optionally ``thread_uuid``) —
      conversational agent memory. ``thread_uuid`` is required for
      :meth:`add_messages` (server-side extraction).
    * **Standalone graph** (``graph_uuid``) — shared / domain knowledge.
      Supports :meth:`search` and :meth:`add` only.

    Provide exactly one of ``user_uuid`` or ``graph_uuid``.

    Attributes:
        name: Unique store identifier used by ``MemoryManager`` tools.
        description: Human-readable summary folded into tool descriptions.
        max_search_results: Default result cap when a caller omits one.
        writable: Whether the store accepts writes.
        extraction: Automatic-extraction config. ``True`` (default when
            writable + user/thread mode) enables server-side extraction via
            :meth:`add_messages` on the manager's default cadence (every 5
            turns). Requires ``user_uuid`` and ``thread_uuid``.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_uuid: str | None = None,
        thread_uuid: str | None = None,
        graph_uuid: str | None = None,
        name: str = DEFAULT_STORE_NAME,
        description: str | None = DEFAULT_STORE_DESCRIPTION,
        max_search_results: int | None = DEFAULT_MAX_SEARCH_RESULTS,
        writable: bool = True,
        extraction: Any = None,
        first_name: str | None = None,
        last_name: str | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str = "Assistant",
        ignore_roles: list[str] | None = None,
        search_scope: Scope = "auto",
        search_reranker: str | None = None,
        search_filters: dict[str, Any] | None = None,
        bfs_origin_node_uuids: list[str] | None = None,
        expose_search_tool: bool = False,
        search_pinned_params: dict[str, Any] | None = None,
        search_hidden_params: set[str] | None = None,
    ) -> None:
        """Initialise the store.

        Args:
            zep_client: An initialised ``AsyncZep`` client (caller owns lifecycle).
            user_uuid: The UUID of the Zep user for user-graph mode.
            thread_uuid: The UUID of the Zep thread used by
                :meth:`add_messages`. Required for server-side extraction in
                user-graph mode.
            graph_uuid: The UUID of a standalone graph (mutually exclusive
                with ``user_uuid``).
            name: Store name exposed to ``MemoryManager`` tools.
            description: Store description exposed to ``MemoryManager`` tools.
            max_search_results: Default search result cap.
            writable: Whether writes are accepted.
            extraction: Extraction config shorthand. Defaults to ``True`` when
                the store is writable and has a ``thread_uuid`` (so
                :meth:`add_messages` can run server-side); otherwise ``None``.
                ``True`` (or an ``ExtractionConfig``) requires writable
                user-graph mode with ``user_uuid`` **and** ``thread_uuid`` —
                construction fails fast otherwise. With Strands' default
                trigger, extraction only fires every **5 turns**, so graph
                building is delayed relative to turn-by-turn persistence;
                call ``memory_manager.flush()`` (or use an every-turn
                trigger) when you need messages sent to Zep sooner.
            first_name: User first name — used for the display name on
                persisted user messages.
            last_name: User last name.
            user_message_name: Display name on persisted user messages.
                Defaults to the user's full name when available.
            assistant_message_name: Display name on persisted assistant messages.
            ignore_roles: Roles to exclude from graph ingestion (still stored in
                thread history).
            search_scope: Default search scope for :meth:`search`. Defaults to
                ``"auto"`` (Zep's assembled Context Block from
                ``graph.get_context``).
            search_reranker: Optional default reranker for :meth:`search`.
            search_filters: Optional filters applied to every search.
            bfs_origin_node_uuids: Optional BFS seed node UUIDs for search.
            expose_search_tool: When ``True``, :meth:`get_tools` returns a
                model-callable graph-search tool.
            search_pinned_params: Pin-or-expose config for the search tool.
            search_hidden_params: Hide search-tool parameters without pinning.

        Raises:
            ValueError: On invalid scoping (neither/both of ``user_uuid``/
                ``graph_uuid``, empty ``name``, ``max_search_results < 1``, or
                extraction enabled without writable user/thread mode).
        """
        if not user_uuid and not graph_uuid:
            raise ValueError("ZepMemoryStore requires either user_uuid or graph_uuid")
        if user_uuid and graph_uuid:
            raise ValueError("ZepMemoryStore accepts only one of user_uuid or graph_uuid")
        if not name.strip():
            raise ValueError("ZepMemoryStore: name must not be empty")
        if max_search_results is not None and max_search_results < 1:
            raise ValueError("ZepMemoryStore: max_search_results must be at least 1")

        self._zep = zep_client
        self.user_uuid = user_uuid
        self.thread_uuid = thread_uuid
        self.graph_uuid = graph_uuid

        self.name = name
        self.description = description
        self.max_search_results = max_search_results
        self.writable = writable

        if extraction is None:
            # Server-side extraction needs add_messages, which needs a thread.
            self.extraction = True if (writable and user_uuid and thread_uuid) else None
        else:
            if _extraction_enabled(extraction):
                _require_extraction_support(
                    writable=writable,
                    user_uuid=user_uuid,
                    thread_uuid=thread_uuid,
                )
            self.extraction = extraction

        self.first_name = first_name
        self.last_name = last_name
        self.ignore_roles = ignore_roles
        self.assistant_message_name = assistant_message_name

        resolved_user_name: str | None
        if user_message_name is not None:
            resolved_user_name = user_message_name
        else:
            full = " ".join(part for part in (first_name, last_name) if part)
            resolved_user_name = full or None
        self.user_message_name = resolved_user_name

        self.search_scope: Scope = search_scope
        self.search_reranker = search_reranker
        self.search_filters = search_filters
        self.bfs_origin_node_uuids = bfs_origin_node_uuids
        self.expose_search_tool = expose_search_tool
        self.search_pinned_params = search_pinned_params
        self.search_hidden_params = search_hidden_params

        self._graph = GraphUuidResolver(zep_client, user_uuid=user_uuid, graph_uuid=graph_uuid)

    # ------------------------------------------------------------------
    # MemoryStore contract
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Intentionally performs no Zep calls; provisioning is deferred.

        ``Agent.__init__`` is synchronous, so Strands runs this hook on a
        throwaway event loop in a worker thread. Issuing Zep calls here would
        drive the caller's ``AsyncZep`` client from a second event loop, and
        any connection the caller already opened raises ``RuntimeError: ... is
        bound to a different event loop``. Every Zep call therefore happens on
        the first search or write, which always runs on the agent's own loop.

        Create the user and the thread out of band with
        :func:`~zep_strands.provisioning.create_user` and
        :func:`~zep_strands.provisioning.create_thread`, and give their UUIDs
        to the store.
        """
        return

    async def search(self, query: str, options: SearchOptions | None = None) -> list[MemoryEntry]:
        """Search the Zep graph for entries matching ``query``.

        Args:
            query: The search query text.
            options: Optional search configuration (``max_search_results``).

        Returns:
            Matching memory entries ordered by relevance. For
            ``search_scope="auto"`` this is typically a single entry holding
            Zep's assembled Context Block.

        Raises:
            ValueError: If ``options.max_search_results`` is less than 1.
        """
        caller_max = options.get("max_search_results") if options is not None else None
        if caller_max is not None and caller_max < 1:
            raise ValueError("ZepMemoryStore: max_search_results must be at least 1")
        limit = caller_max or self.max_search_results or DEFAULT_MAX_SEARCH_RESULTS
        limit = min(max(limit, 1), MAX_SEARCH_LIMIT)

        if not query or not query.strip():
            return []

        search_kwargs: dict[str, Any] = {
            "query": query.strip()[:400],
            "scope": self.search_scope,
            "limit": limit,
        }
        if self.search_scope == "auto":
            # graph.get_context assembles the Context Block itself and takes
            # no result cap.
            del search_kwargs["limit"]
        if self.search_reranker is not None:
            if self.search_scope == "auto" and self.search_reranker in _AUTO_INCOMPATIBLE_RERANKERS:
                logger.warning(
                    "ZepMemoryStore search_reranker %r is invalid for scope='auto'; omitting.",
                    self.search_reranker,
                )
            elif self.search_scope != "auto":
                search_kwargs["reranker"] = self.search_reranker
            # auto + compatible reranker: still omit — auto ignores it.
        if self.search_filters is not None:
            search_kwargs["filters"] = self.search_filters
        if self.bfs_origin_node_uuids is not None and self.search_scope != "auto":
            search_kwargs["bfs_origin_node_uuids"] = self.bfs_origin_node_uuids

        results = await run_graph_search(
            self._zep,
            graph_uuid=await self._graph.resolve(),
            **search_kwargs,
        )
        return _results_to_entries(results, limit=limit)

    async def add(self, content: str, metadata: Metadata | None = None) -> Any:
        """Add a single piece of content to the Zep graph via ``graph.episode.add``.

        ``text`` and ``message`` payloads over Zep's size limit are truncated
        with a warning. ``json`` payloads are **not** truncated -- slicing JSON
        strips its closing syntax and Zep would reject the result -- so an
        oversize JSON document raises instead; split it before adding.

        A Zep failure propagates to the caller by design; see the module
        docstring for the failure-handling contract.

        Args:
            content: Text (or JSON string) to ingest.
            metadata: Optional metadata. Use ``metadata["type"]`` to select the
                Zep data type (``"text"`` default, ``"json"``, or ``"message"``).
                Remaining keys are forwarded as episode metadata when present.

        Returns:
            The result of ``graph.episode.add``.

        Raises:
            ValueError: If the store is not writable, ``content`` is empty, or
                a ``json`` payload exceeds the episode size limit.
        """
        if not self.writable:
            raise ValueError(
                "ZepMemoryStore: store is not writable. Set writable=True to enable add()."
            )
        if not content or not str(content).strip():
            raise ValueError("ZepMemoryStore: content must not be empty")

        meta = dict(metadata or {})
        raw_type = meta.pop(ADD_TYPE_METADATA_KEY, "text")
        data_type: GraphAddType
        if raw_type in ("text", "json", "message"):
            data_type = raw_type  # type: ignore[assignment]
        else:
            raise ValueError(
                f"ZepMemoryStore: metadata['type'] must be 'text', 'json', or 'message'; "
                f"got {raw_type!r}"
            )

        if data_type == "json":
            payload = content if isinstance(content, str) else json.dumps(content)
            # Truncating JSON would strip closing syntax and produce a payload Zep
            # rejects, so the "guard" would guarantee a failed write. Structure-aware
            # splitting needs the caller's schema knowledge -- surface it instead.
            if len(payload) > GRAPH_DATA_TRUNCATE_LIMIT:
                raise ValueError(
                    f"ZepMemoryStore: JSON content is {len(payload)} characters, exceeding the "
                    f"{GRAPH_DATA_TRUNCATE_LIMIT}-character episode limit. JSON cannot be "
                    "truncated safely; split it into smaller documents before adding. See "
                    "https://help.getzep.com/chunking-large-documents"
                )
        else:
            payload = truncate_graph_data(str(content), label="add() content")

        add_kwargs: dict[str, Any] = {"type": data_type, "data": payload}
        if meta:
            add_kwargs["metadata"] = meta

        return await self._zep.graph.episode.add(await self._graph.resolve(), **add_kwargs)

    async def add_messages(
        self, messages: list[Message], context: AddMessagesContext | None = None
    ) -> Any:
        """Ingest a batch of conversation messages for server-side extraction.

        Converts Strands messages to Zep thread messages and calls
        ``thread.add_messages``. Requires user-graph mode with a
        ``thread_uuid``.

        Args:
            messages: Strands conversation messages (role + content blocks).
            context: Optional manager context (sequence numbers for idempotency;
                currently unused — Zep deduplicates server-side).

        Returns:
            The Zep ``add_messages`` response, or ``None`` when there is nothing
            to persist.

        Raises:
            ValueError: If the store is not writable, has no ``thread_uuid``,
                or is in standalone-graph mode.
        """
        del context  # reserved for future idempotency keys
        if not self.writable:
            raise ValueError(
                "ZepMemoryStore: store is not writable. Set writable=True to enable add_messages()."
            )
        if self.graph_uuid or not self.user_uuid:
            raise ValueError(
                "ZepMemoryStore.add_messages requires user-graph mode (user_uuid); "
                "standalone graphs use add() instead."
            )
        if not self.thread_uuid:
            raise ValueError(
                "ZepMemoryStore.add_messages requires thread_uuid for conversation ingestion."
            )

        zep_messages = self._to_zep_messages(messages)
        if not zep_messages:
            return None

        add_kwargs: dict[str, Any] = {"messages": zep_messages}
        if self.ignore_roles:
            add_kwargs["ignore_roles"] = self.ignore_roles

        return await self._zep.thread.add_messages(self.thread_uuid, **add_kwargs)

    def get_tools(self) -> list[AgentTool]:
        """Return store-specific tools when ``expose_search_tool`` is enabled."""
        if not self.expose_search_tool:
            return []
        return [
            create_zep_search_tool(
                zep_client=self._zep,
                user_uuid=self.user_uuid,
                graph_uuid=self.graph_uuid,
                search_pinned_params=self.search_pinned_params,
                search_hidden_params=self.search_hidden_params,
                search_filters=self.search_filters,
                bfs_origin_node_uuids=self.bfs_origin_node_uuids,
            )
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _to_zep_messages(self, messages: list[Message]) -> list[AddMessage]:
        """Convert Strands messages to Zep ``AddMessage`` objects, dropping empties."""
        converted: list[AddMessage] = []
        for message in messages:
            role = _role_to_zep(str(message.get("role", "user")))
            text = _extract_text(message)
            if not text:
                continue
            text = truncate_message_content(text, label=f"{role} message")
            name: str | None = None
            if role == "user":
                name = self.user_message_name
            elif role == "assistant":
                name = self.assistant_message_name
            converted.append(AddMessage(role=role, content=text, name=name))
        return converted


# Re-export helpers used by tests / advanced callers.
__all__ = [
    "ADD_TYPE_METADATA_KEY",
    "DEFAULT_MAX_SEARCH_RESULTS",
    "DEFAULT_STORE_DESCRIPTION",
    "DEFAULT_STORE_NAME",
    "ZepMemoryStore",
    "_results_to_entries",
]
