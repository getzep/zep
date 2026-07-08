"""
The Zep history processor for Pydantic AI.

Pydantic AI runs a *history processor* (registered via
``capabilities=[ProcessHistory(fn)]``) immediately before **every** model
request.  This integration uses that hook to do two things on the user's turn:

1. **Persist** the latest user message to Zep via
   ``thread.add_messages(return_context=True)`` -- folding write and retrieval
   into a single round-trip.  When a custom ``context_builder`` is configured
   on ``ZepDeps``, persistence (without ``return_context``) and the builder run
   concurrently instead -- see :data:`zep_pydantic_ai.deps.ContextBuilder`.
2. **Prepend** the resulting context block to the message history as a system
   ``ModelRequest`` (wrapped in ``context_template``) so the model sees the
   user's long-term memory before the conversation.

The critical subtlety (verified against Pydantic AI 1.107): the processor fires
**once per model request, not once per run**.  A single ``agent.run`` that makes
a tool call therefore invokes the processor at least twice with the *same* user
turn.  Persisting on every invocation would create duplicate Zep episodes, so
the processor **dedupes per run**: the cache is keyed by the Pydantic AI
``RunContext.run_id``, so it persists + retrieves on the first model request of a
run, caches the retrieved context, and on re-invocations within the *same* run
simply re-prepends the cached context without touching Zep again.  Keying on the
run (rather than on the user text) is deliberate: two consecutive runs that send
the *identical* user message are genuine repeat turns and must each be persisted.

Assistant responses are **not** persisted here (the model has not produced them
yet at request time).  Persist them after the run with
:func:`zep_pydantic_ai.history_processor.persist_run`, or automatically via
:func:`zep_pydantic_ai.capabilities.create_zep_after_run_hook`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage

from .deps import (
    DEFAULT_CONTEXT_TEMPLATE,
    ContextInput,
    ZepDeps,
    ensure_user_and_thread,
    latest_user_text,
    make_context_request,
    model_messages_to_zep,
    truncate_message_content,
)

logger = logging.getLogger(__name__)

#: Re-exported for convenience -- see ``zep_pydantic_ai.deps`` for the
#: canonical definitions (``ZepDeps.context_builder`` / ``.context_template``
#: are typed against them, so they live there to avoid a circular import).
__all__ = [
    "HistoryProcessorFn",
    "DEFAULT_CONTEXT_TEMPLATE",
    "ContextInput",
    "zep_history_processor",
    "persist_run",
    "reset_turn_cache",
]

#: Signature of the history processor registered with ``ProcessHistory``.
HistoryProcessorFn = Callable[
    [RunContext[ZepDeps], list[ModelMessage]],
    Awaitable[list[ModelMessage]],
]

#: Maximum number of runs kept in the dedupe cache.  The processor fires once
#: per model request; the cache only needs an entry per *in-flight* run, but we
#: keep a small backlog so concurrent runs in a busy process all dedupe
#: correctly.  Oldest entries are evicted first (LRU).
_CACHE_MAX_ENTRIES = 1024


class _RunDedupeCache:
    """A bounded, lock-guarded LRU of per-run retrieved context.

    Keyed by the Pydantic AI ``RunContext.run_id`` so each ``agent.run``
    persists its user turn exactly once, while two consecutive runs that send
    the *same* user text each persist (genuine repeat turns).  Bounded so a
    long-lived process serving many runs never grows the cache without limit;
    guarded by a lock so concurrent runs do not corrupt it.
    """

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._max = max_entries
        self._data: OrderedDict[str, str | None] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, run_key: str) -> tuple[bool, str | None]:
        """Return ``(hit, context)`` for ``run_key``; refreshes LRU order."""
        async with self._lock:
            if run_key not in self._data:
                return False, None
            self._data.move_to_end(run_key)
            return True, self._data[run_key]

    async def set(self, run_key: str, context: str | None) -> None:
        """Store ``context`` for ``run_key``, evicting the oldest if full."""
        async with self._lock:
            self._data[run_key] = context
            self._data.move_to_end(run_key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


_turn_cache = _RunDedupeCache()


def _run_key(ctx: RunContext[ZepDeps], deps: ZepDeps) -> str:
    """Build the dedupe key for this run.

    Prefers ``RunContext.run_id`` (one per ``agent.run``).  Falls back to the
    ``(user_id, thread_id)`` pair only if a run id is unavailable, so dedupe
    still degrades gracefully on older Pydantic AI versions.
    """
    run_id = getattr(ctx, "run_id", None)
    if run_id:
        return f"run:{run_id}"
    return f"thread:{deps.user_id}:{deps.thread_id}"


async def zep_history_processor(
    ctx: RunContext[ZepDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Persist the latest user turn to Zep and prepend the context block.

    Register this with a Pydantic AI agent::

        from pydantic_ai import Agent
        from pydantic_ai.capabilities import ProcessHistory
        from zep_pydantic_ai import ZepDeps, zep_history_processor

        agent = Agent(
            "openai:gpt-4o-mini",
            deps_type=ZepDeps,
            capabilities=[ProcessHistory(zep_history_processor)],
        )

    On each model request the processor:

    * resolves the Zep client + identity from ``ctx.deps``;
    * lazily creates the Zep user and thread;
    * extracts the latest user message text (truncated to Zep's per-message
      limit);
    * if this run has not yet persisted its user turn (run-scoped dedupe
      guard):

      * when ``ctx.deps.context_builder`` is ``None`` (the default), calls
        ``thread.add_messages(return_context=True)`` -- a single round-trip
        that persists and retrieves context together;
      * when a ``context_builder`` is set, persists via ``add_messages``
        (without ``return_context``) and runs the builder **concurrently**
        via ``asyncio.gather``.  Each side is isolated from the other's
        failure: a builder error is logged and skips injection but does not
        stop persistence; a persistence error is logged (and the turn is not
        marked as persisted, so it can be retried) but a successful builder
        result is still injected.

    * caches the retrieved/built context so replay on re-invocations within
      the same run makes no further Zep calls;
    * prepends the context block (fresh or cached), wrapped in
      ``ctx.deps.context_template``, to ``messages`` as a system
      ``ModelRequest``.

    All Zep failures are caught and logged; the original ``messages`` are
    returned unchanged so a Zep outage never breaks the agent run.

    Args:
        ctx: The Pydantic AI run context; ``ctx.deps`` must be a ``ZepDeps``.
        messages: The current message history for this model request.

    Returns:
        ``messages`` with Zep's context block prepended (when available).
    """
    deps = ctx.deps
    if deps is None:  # pragma: no cover - defensive; deps_type guarantees this
        logger.warning("zep_history_processor: ctx.deps is None; skipping Zep")
        return messages

    user_text = latest_user_text(messages)
    if not user_text:
        # No user turn to anchor on (e.g. a tool-only continuation with no
        # extractable text). Nothing to persist; pass history through.
        return messages

    run_key = _run_key(ctx, deps)
    hit, cached_context = await _turn_cache.get(run_key)

    # --- Re-invocation within the same run: replay cached context -----------
    if hit:
        return _with_context(messages, cached_context, deps.context_template)

    # --- First model request of this run: persist + retrieve ---------------
    # Truncate on the hot path before sending; Zep rejects content >4096 chars
    # with HTTP 400.  Warn with lengths only (never content / PII).
    user_text = truncate_message_content(user_text, label="user turn")

    try:
        resources_ready = await ensure_user_and_thread(deps)
    except Exception:
        logger.warning(
            "Failed to provision Zep user/thread for the turn path",
            exc_info=True,
        )
        return messages

    if not resources_ready:
        return messages

    if deps.context_builder is not None:
        persist_ok, context = await _persist_and_build_context(ctx, deps, user_text)
    else:
        persist_ok, context = await _persist_with_return_context(deps, user_text)

    # Only cache after a successful persist so a transient failure can be
    # retried on the next model request of this run.
    if persist_ok:
        await _turn_cache.set(run_key, context)

    return _with_context(messages, context, deps.context_template)


async def _persist_with_return_context(deps: ZepDeps, user_text: str) -> tuple[bool, str | None]:
    """Persist the user message and retrieve context in one round-trip.

    Returns:
        A ``(persist_ok, context)`` tuple.  On failure, logs a warning and
        returns ``(False, None)``.
    """
    from zep_cloud import Message  # local import keeps module import light

    try:
        response = await deps.client.thread.add_messages(
            thread_id=deps.thread_id,
            messages=[Message(role="user", content=user_text, name=deps.display_name)],
            return_context=True,
            ignore_roles=deps.ignore_roles,
        )
    except Exception:
        logger.warning(
            "Failed to persist user turn / retrieve context from Zep",
            exc_info=True,
        )
        return False, None

    context = response.context if response else None
    logger.info(
        "Persisted user turn to Zep (thread=%s); context length=%s",
        deps.thread_id,
        len(context) if context else 0,
    )
    return True, context


async def _persist_and_build_context(
    ctx: RunContext[ZepDeps],
    deps: ZepDeps,
    user_text: str,
) -> tuple[bool, str | None]:
    """Persist the message and run ``context_builder`` concurrently.

    Runs ``thread.add_messages`` (without ``return_context``) and the custom
    ``context_builder`` concurrently via ``asyncio.gather(..., return_exceptions=True)``
    so one side's exception can never cancel or mask the other's result.

    * If the builder raises, a warning is logged and ``None`` is used for the
      context, but persistence is unaffected.
    * If persistence raises, a warning is logged and ``persist_ok=False`` is
      returned (so the caller does not mark the turn as persisted / dedup'd),
      but a successful builder result is still returned for injection.

    Returns:
        A ``(persist_ok, context)`` tuple.
    """
    from zep_cloud import Message  # local import keeps module import light

    context_builder = deps.context_builder
    assert context_builder is not None  # caller (zep_history_processor) already checked

    async def _persist() -> None:
        await deps.client.thread.add_messages(
            thread_id=deps.thread_id,
            messages=[Message(role="user", content=user_text, name=deps.display_name)],
            ignore_roles=deps.ignore_roles,
        )
        logger.info("Persisted user turn to Zep (thread=%s).", deps.thread_id)

    async def _build() -> str | None:
        context_input = ContextInput(
            zep=deps.client,
            user_id=deps.user_id,
            thread_id=deps.thread_id,
            user_message=user_text,
            run_context=ctx,
        )
        return await context_builder(context_input)

    persist_result, build_result = await asyncio.gather(
        _persist(), _build(), return_exceptions=True
    )

    if isinstance(build_result, BaseException):
        logger.warning(
            "Custom context_builder raised — skipping context injection for this turn",
            exc_info=build_result,
        )
        context: str | None = None
    else:
        context = build_result

    if isinstance(persist_result, BaseException):
        logger.warning(
            "Failed to persist user turn to Zep",
            exc_info=persist_result,
        )
        return False, context

    return True, context


def _with_context(
    messages: list[ModelMessage],
    context: str | None,
    context_template: str,
) -> list[ModelMessage]:
    """Prepend the Zep context block to the history, if any."""
    if not context:
        return messages
    return [make_context_request(context, template=context_template), *messages]


async def persist_run(
    deps: ZepDeps,
    new_messages: list[ModelMessage],
) -> None:
    """Persist the assistant messages produced by a run to the Zep thread.

    Call this after ``agent.run`` with ``result.new_messages()`` to store the
    assistant's reply (the user turn is already persisted by the history
    processor)::

        result = await agent.run("Hi", deps=deps)
        await persist_run(deps, result.new_messages())

    Only **assistant** text messages from ``new_messages`` are sent -- the user
    turn (already in Zep) and any tool-call / tool-return scaffolding are
    skipped, so Zep sees exactly one clean assistant message per turn.  If there
    is no assistant text, this is a no-op.

    Failures are caught and logged; this never raises.

    Args:
        deps: The dependency object carrying the client and identity.
        new_messages: Messages from ``result.new_messages()``.
    """
    zep_messages = [
        m
        for m in model_messages_to_zep(
            new_messages,
            user_name=deps.display_name,
            assistant_name=deps.assistant_name,
        )
        if m.role == "assistant"
    ]
    if not zep_messages:
        return

    try:
        if await ensure_user_and_thread(deps):
            await deps.client.thread.add_messages(
                thread_id=deps.thread_id,
                messages=zep_messages,
                ignore_roles=deps.ignore_roles,
            )
            logger.info(
                "Persisted %d assistant message(s) to Zep thread %s",
                len(zep_messages),
                deps.thread_id,
            )
    except Exception:
        logger.warning("Failed to persist assistant messages to Zep", exc_info=True)


def reset_turn_cache() -> None:
    """Clear the in-process run-dedupe cache.

    Primarily useful in tests; production code rarely needs this.  The cache is
    a bounded LRU keyed by run id and evicts old entries automatically as runs
    complete, so it never grows without limit.
    """
    _turn_cache.clear()
