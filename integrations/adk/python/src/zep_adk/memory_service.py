"""
ZepMemoryService -- an ADK-native ``BaseMemoryService`` backed by Zep.

ADK's memory extension point (``google.adk.memory.base_memory_service.BaseMemoryService``)
lets a ``Runner`` search long-term memory through a model-opt-in tool
(``load_memory`` / ``preload_memory``): the model decides when to call
``tool_context.search_memory(query)``, which reaches this service via
``Runner(..., memory_service=ZepMemoryService(...))``.

This is a different extension point from :class:`~zep_adk.context_tool.ZepContextTool`:

* **ZepMemoryService** (this module) -- ADK-native, model-opt-in. The model
  decides, per turn, whether to call ``load_memory``/``preload_memory``. Use
  this when you want the model to actively decide when memory is relevant,
  or when wiring into ADK code paths that already expect a
  ``memory_service`` (e.g. ADK's own memory tools, evaluation harnesses).
* **ZepContextTool** -- guaranteed injection. Runs on every LLM turn via
  ``process_llm_request``, so Zep context is always present regardless of
  whether the model would have thought to ask for it. Use this for the
  common case: an assistant that should always have the user's long-term
  context available.

The two are complementary, not mutually exclusive -- e.g. use
``ZepContextTool`` for always-on context and additionally register
``ZepMemoryService`` so the model can explicitly search for more via
``load_memory`` when it decides the always-on context wasn't enough.

Why ``add_session_to_memory`` is a no-op: Zep persistence already happens
live, on every turn, via :class:`~zep_adk.context_tool.ZepContextTool` (or a
custom integration using ``thread.add_messages``) and
:func:`~zep_adk.callbacks.create_after_model_callback`. ADK calls
``add_session_to_memory`` to flush a session's conversation into a memory
store at some point after it happens (e.g. session end); since Zep already
has every message as it's persisted turn-by-turn, doing it again here would
re-ingest the same conversation into the graph a second time. This mirrors
the Go integration's ``NewMemoryService``, whose ``AddSessionToMemory`` is
the same intentional no-op for the same reason.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types
from typing_extensions import override
from zep_cloud.client import AsyncZep

from .graph_search_tool import scope_results_to_texts

if TYPE_CHECKING:
    from google.adk.sessions import Session

logger = logging.getLogger(__name__)

#: Scopes handled by treating ``result.context`` as a single pre-materialized
#: Context Block, rather than a list of discrete edges/nodes/etc.
_AUTO_SCOPE = "auto"

#: Scopes supported by :meth:`ZepMemoryService.search_memory`. Matches the
#: scope enum exposed by :class:`~zep_adk.graph_search_tool.ZepGraphSearchTool`.
_SUPPORTED_SCOPES = frozenset(
    {"edges", "nodes", "episodes", "observations", "thread_summaries", _AUTO_SCOPE}
)


class ZepMemoryService(BaseMemoryService):
    """ADK-native memory service that searches a user's Zep knowledge graph.

    Register this on a ``Runner`` to give ADK's built-in memory tooling
    (``load_memory``, ``preload_memory``) access to Zep:

    .. code-block:: python

        from google.adk.runners import Runner
        from google.adk.tools import load_memory
        from zep_adk import ZepMemoryService

        agent = Agent(
            name="my_agent",
            model="gemini-2.5-flash",
            tools=[load_memory],
        )
        runner = Runner(
            agent=agent,
            app_name="my_app",
            session_service=session_service,
            memory_service=ZepMemoryService(zep=zep),
        )

    See the module docstring for guidance on when to use this versus
    :class:`~zep_adk.context_tool.ZepContextTool`.

    Args:
        zep: An initialised ``AsyncZep`` client.
        scope: The Zep graph search scope to use for every ``search_memory``
            call. Matches :class:`~zep_adk.graph_search_tool.ZepGraphSearchTool`'s
            scope enum: ``"edges"`` (facts, the default), ``"nodes"``
            (entities and summaries), ``"episodes"`` (raw message/data
            content), ``"observations"`` (derived memories),
            ``"thread_summaries"`` (incremental thread summaries), or
            ``"auto"`` (Zep's own pre-assembled Context Block, returned as a
            single memory entry).
        limit: Maximum number of results per search. ``None`` (the default)
            omits the parameter so the Zep SDK applies its own default.
    """

    def __init__(
        self,
        *,
        zep: AsyncZep,
        scope: str = "edges",
        limit: int | None = None,
    ) -> None:
        self._zep: AsyncZep = zep
        self._scope: str = scope
        self._limit: int | None = limit

    @override
    async def add_session_to_memory(self, session: Session) -> None:
        """No-op. See the module docstring for the no-double-persist rationale.

        Conversation turns are already persisted live via
        :class:`~zep_adk.context_tool.ZepContextTool` (or an equivalent
        ``thread.add_messages`` call) and
        :func:`~zep_adk.callbacks.create_after_model_callback`. Ingesting the
        session again here would duplicate that work in Zep's graph.
        """
        logger.debug(
            "ZepMemoryService.add_session_to_memory is a no-op: Zep already "
            "ingests conversation turns live via ZepContextTool / "
            "create_after_model_callback, so re-ingesting the full session "
            "here would double-persist it."
        )

    @override
    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> SearchMemoryResponse:
        """Search the user's Zep graph and map results to ``MemoryEntry`` objects.

        ``app_name`` has no Zep equivalent -- Zep scopes memory by user
        graph, not by application -- so it is accepted (to satisfy the ADK
        interface) but not forwarded to Zep.

        On any Zep failure, logs a warning (lengths/counts only, never query
        or result content) and returns an empty response rather than raising,
        so a memory lookup never breaks the agent.

        An unsupported scope is rejected before the search is issued: like
        Go's ``memoryService.SearchMemory`` (see ``searchScopeSupported`` in
        ``integrations/adk/go/memory.go``), this avoids spending a live
        network call on a scope we can never map into memory entries.
        """
        if self._scope not in _SUPPORTED_SCOPES:
            logger.warning(
                "Unsupported Zep memory search scope %r; returning no memories",
                self._scope,
            )
            return SearchMemoryResponse()

        search_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "query": query,
            "scope": self._scope,
        }
        if self._limit is not None:
            search_kwargs["limit"] = self._limit

        try:
            result = await self._zep.graph.search(**search_kwargs)
        except Exception as exc:
            logger.warning(
                "Zep memory search failed (user_id_len=%d, query_len=%d): %s",
                len(user_id),
                len(query),
                exc,
            )
            return SearchMemoryResponse()

        texts = self._result_texts(result)
        memories = [
            MemoryEntry(
                content=types.Content(parts=[types.Part(text=text)], role="model"),
                author="Zep",
            )
            for text in texts
        ]
        return SearchMemoryResponse(memories=memories)

    def _result_texts(self, result: Any) -> list[str]:
        """Flatten a ``graph.search`` result into text items for the configured scope."""
        if self._scope == _AUTO_SCOPE:
            context: str | None = getattr(result, "context", None)
            if context and context.strip():
                return [context.strip()]
            return []

        return scope_results_to_texts(result, self._scope)
