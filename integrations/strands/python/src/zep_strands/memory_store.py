"""Zep ``MemoryStore`` for Strands Agents.

:class:`ZepMemoryStore` implements Strands'
:class:`~strands.memory.types.MemoryStore` Protocol so a Zep user graph (or
standalone graph) plugs into :class:`~strands.memory.MemoryManager` like any
other store:

* :meth:`search` recalls relevant knowledge via ``graph.search``
* :meth:`add_messages` ingests conversation turns for **server-side**
  extraction via ``thread.add_messages`` (user-graph mode)
* :meth:`add` writes a single text/JSON fact via ``graph.add``
* :meth:`get_tools` optionally registers an on-demand graph-search tool

The Zep user and thread are provisioned on the store's first search or write.
:meth:`initialize` is deliberately inert; see its docstring for why.

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
        user_id="user-123",
        thread_id="thread-abc",
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
from zep_cloud import Message as ZepMessage
from zep_cloud.client import AsyncZep

from ._text import (
    GRAPH_DATA_TRUNCATE_LIMIT,
    truncate_graph_data,
    truncate_message_content,
)
from .provisioning import UserSetupHook
from .provisioning import ensure_thread as _ensure_thread
from .provisioning import ensure_user as _ensure_user
from .search import (
    _AUTO_INCOMPATIBLE_RERANKERS,
    MAX_SEARCH_LIMIT,
    Scope,
    _name_summary_text,
    create_zep_search_tool,
)

logger = logging.getLogger(__name__)

DEFAULT_STORE_NAME = "zep"
DEFAULT_STORE_DESCRIPTION = (
    "Long-term memory backed by Zep's temporal Context Graph — facts, "
    "entities, and prior conversations about the user."
)
DEFAULT_MAX_SEARCH_RESULTS = 10

#: Metadata key that selects the ``graph.add`` data type for :meth:`ZepMemoryStore.add`.
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
    user_id: str | None,
    thread_id: str | None,
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
    if not user_id or not thread_id:
        raise ValueError(
            "ZepMemoryStore: extraction requires user-graph mode with both "
            "user_id and thread_id (server-side via add_messages). "
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
    """Map a Strands message role onto a Zep thread-message role."""
    normalised = role.lower().strip()
    if normalised in {"user", "assistant", "system", "tool", "function", "norole"}:
        return normalised
    # Strands may emit other roles; keep them as free-text under norole rather
    # than dropping the turn.
    return "norole"


def _results_to_entries(result: Any, scope: str, *, limit: int) -> list[MemoryEntry]:
    """Convert a Zep ``graph.search`` response into Strands ``MemoryEntry`` rows.

    For ``scope="auto"``, prefer Zep's assembled Context Block as a single
    entry (the shape Zep designs for prompt injection). For scoped searches,
    expand individual edges / nodes / episodes / observations / thread
    summaries into separate entries so ``MemoryManager`` can cap and format
    them independently.
    """
    if scope == "auto":
        context = getattr(result, "context", None)
        if context and str(context).strip():
            return [MemoryEntry(content=str(context).strip(), metadata={"scope": "auto"})]
        # Fall through to expand any populated collections when context is empty.

    entries: list[MemoryEntry] = []

    if result.edges:
        for edge in result.edges:
            fact = getattr(edge, "fact", None)
            if fact:
                entries.append(
                    MemoryEntry(
                        content=str(fact),
                        metadata={
                            "scope": "edges",
                            "uuid": getattr(edge, "uuid_", None) or getattr(edge, "uuid", None),
                        },
                    )
                )

    if result.nodes:
        for node in result.nodes:
            text = _name_summary_text(getattr(node, "name", None), getattr(node, "summary", None))
            if text:
                entries.append(
                    MemoryEntry(
                        content=text,
                        metadata={
                            "scope": "nodes",
                            "uuid": getattr(node, "uuid_", None) or getattr(node, "uuid", None),
                        },
                    )
                )

    if result.episodes:
        for ep in result.episodes:
            content = getattr(ep, "content", None)
            if content:
                entries.append(
                    MemoryEntry(
                        content=str(content),
                        metadata={
                            "scope": "episodes",
                            "uuid": getattr(ep, "uuid_", None) or getattr(ep, "uuid", None),
                        },
                    )
                )

    if getattr(result, "observations", None):
        for obs in result.observations:
            text = _name_summary_text(getattr(obs, "name", None), getattr(obs, "summary", None))
            if text:
                entries.append(
                    MemoryEntry(
                        content=text,
                        metadata={
                            "scope": "observations",
                            "uuid": getattr(obs, "uuid_", None) or getattr(obs, "uuid", None),
                        },
                    )
                )

    if getattr(result, "thread_summaries", None):
        for ts in result.thread_summaries:
            summary = getattr(ts, "summary", None) or getattr(ts, "name", None)
            if summary:
                entries.append(
                    MemoryEntry(
                        content=str(summary),
                        metadata={
                            "scope": "thread_summaries",
                            "uuid": getattr(ts, "uuid_", None) or getattr(ts, "uuid", None),
                        },
                    )
                )

    return entries[:limit]


class ZepMemoryStore:
    """Strands :class:`~strands.memory.types.MemoryStore` backed by Zep.

    Two scoping modes:

    * **User graph** (``user_id``, optionally ``thread_id``) — conversational
      agent memory. ``thread_id`` is required for :meth:`add_messages`
      (server-side extraction).
    * **Standalone graph** (``graph_id``) — shared / domain knowledge. Supports
      :meth:`search` and :meth:`add` only.

    Provide exactly one of ``user_id`` or ``graph_id``.

    Attributes:
        name: Unique store identifier used by ``MemoryManager`` tools.
        description: Human-readable summary folded into tool descriptions.
        max_search_results: Default result cap when a caller omits one.
        writable: Whether the store accepts writes.
        extraction: Automatic-extraction config. ``True`` (default when
            writable + user/thread mode) enables server-side extraction via
            :meth:`add_messages` on the manager's default cadence (every 5
            turns). Requires ``user_id`` and ``thread_id``.
    """

    def __init__(
        self,
        *,
        zep_client: AsyncZep,
        user_id: str | None = None,
        thread_id: str | None = None,
        graph_id: str | None = None,
        name: str = DEFAULT_STORE_NAME,
        description: str | None = DEFAULT_STORE_DESCRIPTION,
        max_search_results: int | None = DEFAULT_MAX_SEARCH_RESULTS,
        writable: bool = True,
        extraction: Any = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        user_message_name: str | None = None,
        assistant_message_name: str = "Assistant",
        ignore_roles: list[str] | None = None,
        on_user_created: UserSetupHook | None = None,
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
            user_id: Zep user ID for user-graph mode.
            thread_id: Zep thread ID used by :meth:`add_messages`. Required for
                server-side extraction in user-graph mode.
            graph_id: Standalone graph ID (mutually exclusive with ``user_id``).
            name: Store name exposed to ``MemoryManager`` tools.
            description: Store description exposed to ``MemoryManager`` tools.
            max_search_results: Default search result cap.
            writable: Whether writes are accepted.
            extraction: Extraction config shorthand. Defaults to ``True`` when
                the store is writable and has a ``thread_id`` (so
                :meth:`add_messages` can run server-side); otherwise ``None``.
                ``True`` (or an ``ExtractionConfig``) requires writable
                user-graph mode with ``user_id`` **and** ``thread_id`` —
                construction fails fast otherwise. With Strands' default
                trigger, extraction only fires every **5 turns**, so graph
                building is delayed relative to turn-by-turn persistence;
                call ``memory_manager.flush()`` (or use an every-turn
                trigger) when you need messages sent to Zep sooner.
            first_name: User first name — helps Zep anchor identity.
            last_name: User last name.
            email: User email.
            user_message_name: Display name on persisted user messages.
                Defaults to the user's full name when available.
            assistant_message_name: Display name on persisted assistant messages.
            ignore_roles: Roles to exclude from graph ingestion (still stored in
                thread history).
            on_user_created: Async hook run once after a new user is created.
            search_scope: Default ``graph.search`` scope for :meth:`search`.
                Defaults to ``"auto"`` (Zep's assembled Context Block).
            search_reranker: Optional default reranker for :meth:`search`.
            search_filters: Optional filters applied to every search.
            bfs_origin_node_uuids: Optional BFS seed node UUIDs for search.
            expose_search_tool: When ``True``, :meth:`get_tools` returns a
                model-callable graph-search tool.
            search_pinned_params: Pin-or-expose config for the search tool.
            search_hidden_params: Hide search-tool parameters without pinning.

        Raises:
            ValueError: On invalid scoping (neither/both of ``user_id``/
                ``graph_id``, empty ``name``, ``max_search_results < 1``, or
                extraction enabled without writable user/thread mode).
        """
        if not user_id and not graph_id:
            raise ValueError("ZepMemoryStore requires either user_id or graph_id")
        if user_id and graph_id:
            raise ValueError("ZepMemoryStore accepts only one of user_id or graph_id")
        if not name.strip():
            raise ValueError("ZepMemoryStore: name must not be empty")
        if max_search_results is not None and max_search_results < 1:
            raise ValueError("ZepMemoryStore: max_search_results must be at least 1")

        self._zep = zep_client
        self.user_id = user_id
        self.thread_id = thread_id
        self.graph_id = graph_id

        self.name = name
        self.description = description
        self.max_search_results = max_search_results
        self.writable = writable

        if extraction is None:
            # Server-side extraction needs add_messages, which needs a thread.
            self.extraction = True if (writable and user_id and thread_id) else None
        else:
            if _extraction_enabled(extraction):
                _require_extraction_support(
                    writable=writable,
                    user_id=user_id,
                    thread_id=thread_id,
                )
            self.extraction = extraction

        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.ignore_roles = ignore_roles
        self.on_user_created = on_user_created
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

        self._resources_ready = False

    # ------------------------------------------------------------------
    # MemoryStore contract
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Intentionally performs no Zep calls; provisioning is deferred.

        ``Agent.__init__`` is synchronous, so Strands runs this hook on a
        throwaway event loop in a worker thread. Issuing Zep calls here would
        drive the caller's ``AsyncZep`` client from a second event loop, and
        any connection the caller already opened raises ``RuntimeError: ... is
        bound to a different event loop``. Provisioning therefore happens on
        the first search or write, which always runs on the agent's own loop.

        Call :func:`~zep_strands.provisioning.ensure_user` and
        :func:`~zep_strands.provisioning.ensure_thread` before constructing the
        agent to provision eagerly instead.
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
            search_kwargs["search_filters"] = self.search_filters
        if self.bfs_origin_node_uuids is not None:
            search_kwargs["bfs_origin_node_uuids"] = self.bfs_origin_node_uuids

        if self.graph_id:
            search_kwargs["graph_id"] = self.graph_id
        else:
            search_kwargs["user_id"] = self.user_id

        await self._ensure_resources_lazy()

        results = await self._zep.graph.search(**search_kwargs)
        return _results_to_entries(results, self.search_scope, limit=limit)

    async def add(self, content: str, metadata: Metadata | None = None) -> Any:
        """Add a single piece of content to the Zep graph via ``graph.add``.

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
            The Zep episode created by ``graph.add``.

        Raises:
            ValueError: If the store is not writable, ``content`` is empty, or
                a ``json`` payload exceeds the ``graph.add`` size limit.
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
                    f"{GRAPH_DATA_TRUNCATE_LIMIT}-character graph.add limit. JSON cannot be "
                    "truncated safely; split it into smaller documents before adding. See "
                    "https://help.getzep.com/chunking-large-documents"
                )
        else:
            payload = truncate_graph_data(str(content), label="add() content")

        add_kwargs: dict[str, Any] = {"type": data_type, "data": payload}
        if meta:
            add_kwargs["metadata"] = meta
        if self.graph_id:
            add_kwargs["graph_id"] = self.graph_id
        else:
            await self._ensure_resources_lazy()
            add_kwargs["user_id"] = self.user_id

        return await self._zep.graph.add(**add_kwargs)

    async def add_messages(
        self, messages: list[Message], context: AddMessagesContext | None = None
    ) -> Any:
        """Ingest a batch of conversation messages for server-side extraction.

        Converts Strands messages to Zep thread messages and calls
        ``thread.add_messages``. Requires user-graph mode with a ``thread_id``.

        Args:
            messages: Strands conversation messages (role + content blocks).
            context: Optional manager context (sequence numbers for idempotency;
                currently unused — Zep deduplicates server-side).

        Returns:
            The Zep ``add_messages`` response, or ``None`` when there is nothing
            to persist.

        Raises:
            ValueError: If the store is not writable, has no ``thread_id``, or
                is in standalone-graph mode.
        """
        del context  # reserved for future idempotency keys
        if not self.writable:
            raise ValueError(
                "ZepMemoryStore: store is not writable. Set writable=True to enable add_messages()."
            )
        if self.graph_id or not self.user_id:
            raise ValueError(
                "ZepMemoryStore.add_messages requires user-graph mode (user_id); "
                "standalone graphs use add() instead."
            )
        if not self.thread_id:
            raise ValueError(
                "ZepMemoryStore.add_messages requires thread_id for conversation ingestion."
            )

        zep_messages = self._to_zep_messages(messages)
        if not zep_messages:
            return None

        await self._ensure_resources_lazy()

        add_kwargs: dict[str, Any] = {
            "thread_id": self.thread_id,
            "messages": zep_messages,
        }
        if self.ignore_roles:
            add_kwargs["ignore_roles"] = self.ignore_roles

        return await self._zep.thread.add_messages(**add_kwargs)

    def get_tools(self) -> list[AgentTool]:
        """Return store-specific tools when ``expose_search_tool`` is enabled."""
        if not self.expose_search_tool:
            return []
        return [
            create_zep_search_tool(
                zep_client=self._zep,
                user_id=self.user_id,
                graph_id=self.graph_id,
                search_pinned_params=self.search_pinned_params,
                search_hidden_params=self.search_hidden_params,
                search_filters=self.search_filters,
                bfs_origin_node_uuids=self.bfs_origin_node_uuids,
            )
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_resources_lazy(self) -> None:
        """Provision the Zep user and thread once, on first use.

        Standalone-graph mode is a no-op (the graph must already exist).
        Genuine provisioning failures propagate, so misconfiguration surfaces
        on the first turn rather than being swallowed.
        """
        if self._resources_ready or self.graph_id:
            return
        assert self.user_id is not None  # validated in __init__
        await _ensure_user(
            self._zep,
            user_id=self.user_id,
            first_name=self.first_name,
            last_name=self.last_name,
            email=self.email,
            on_created=self.on_user_created,
        )
        if self.thread_id:
            await _ensure_thread(self._zep, thread_id=self.thread_id, user_id=self.user_id)
        self._resources_ready = True

    def _to_zep_messages(self, messages: list[Message]) -> list[ZepMessage]:
        """Convert Strands messages to Zep ``Message`` objects, dropping empties."""
        converted: list[ZepMessage] = []
        for message in messages:
            role = _role_to_zep(str(message.get("role", "norole")))
            text = _extract_text(message)
            if not text:
                continue
            text = truncate_message_content(text, label=f"{role} message")
            name: str | None = None
            if role == "user":
                name = self.user_message_name
            elif role == "assistant":
                name = self.assistant_message_name
            converted.append(ZepMessage(role=role, content=text, name=name))
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
