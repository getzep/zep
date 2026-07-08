"""
Convenience Pydantic AI capability wiring for Zep.

:func:`zep_history_processor` (registered via ``ProcessHistory``) persists the
*user* turn and injects context.  Assistant replies are persisted separately,
either explicitly with :func:`~zep_pydantic_ai.history_processor.persist_run`
after ``agent.run``, or automatically with the ``after_run`` hook built here.

``pydantic_ai.capabilities.Hooks`` exposes an ``after_run`` lifecycle hook
that fires once the run has produced its final result, with the result
available as ``AgentRunResult``.  :func:`create_zep_after_run_hook` builds an
``after_run``-compatible callable that persists ``result.new_messages()`` to
Zep automatically; :func:`zep_capabilities` bundles it with
``ProcessHistory(zep_history_processor)`` for one-line agent wiring::

    from pydantic_ai import Agent
    from zep_pydantic_ai import ZepDeps, zep_capabilities

    agent = Agent(
        "openai:gpt-4o-mini",
        deps_type=ZepDeps,
        capabilities=zep_capabilities(deps),
    )

    result = await agent.run("Hi", deps=deps)
    # No explicit persist_run call needed -- the assistant reply is already
    # in Zep by the time agent.run returns.

``persist_run`` remains exported for callers who prefer explicit control over
when the assistant reply is persisted (e.g. to skip persistence for a given
turn, or to batch it with other work).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, Hooks, ProcessHistory
from pydantic_ai.run import AgentRunResult

from .deps import ZepDeps
from .history_processor import HistoryProcessorFn, persist_run, zep_history_processor

logger = logging.getLogger(__name__)

#: Signature of the callable ``create_zep_after_run_hook`` returns -- matches
#: ``pydantic_ai.capabilities.hooks.AfterRunHookFunc`` (an internal protocol,
#: not part of the public ``pydantic_ai.capabilities`` surface, so we mirror
#: its shape here rather than importing it).
ZepAfterRunHook = Callable[..., Awaitable[AgentRunResult[Any]]]


def create_zep_after_run_hook(
    deps: ZepDeps,
) -> ZepAfterRunHook:
    """Build an ``after_run`` hook that auto-persists the assistant's reply.

    Register the returned callable with ``Hooks(after_run=...)``, or use
    :func:`zep_capabilities` for one-line wiring.  On every run it calls
    :func:`~zep_pydantic_ai.history_processor.persist_run` with
    ``result.new_messages()``.

    This hook runs **inside the agent's run loop**, so the hot-path rule
    applies: any Zep failure (including the lazy provisioning call inside
    ``persist_run``) is caught and logged, never raised into the run --
    ``persist_run`` already implements this contract, so this hook simply
    delegates to it and always returns ``result`` unchanged.

    Args:
        deps: The dependency object carrying the client and identity.  The
            same ``deps`` instance passed to ``agent.run(..., deps=deps)``
            should be passed here, since the hook closes over it rather than
            reading it from ``ctx`` (``Hooks`` is constructed once, before
            the run starts, and ``ctx.deps`` is only available once the run
            is underway).

    Returns:
        An ``after_run``-compatible callable: ``(ctx, *, result) -> result``.
    """

    async def _after_run(
        ctx: RunContext[Any],
        *,
        result: AgentRunResult[Any],
    ) -> AgentRunResult[Any]:
        await persist_run(deps, result.new_messages())
        return result

    return _after_run


def zep_capabilities(
    deps: ZepDeps,
    *,
    history_processor: HistoryProcessorFn = zep_history_processor,
) -> list[AbstractCapability[Any]]:
    """Build the standard set of Pydantic AI capabilities for Zep memory.

    Bundles :func:`~zep_pydantic_ai.history_processor.zep_history_processor`
    (via ``ProcessHistory``, persisting the user turn and injecting context)
    with an ``after_run`` hook (via ``Hooks``, from
    :func:`create_zep_after_run_hook`) that automatically persists the
    assistant's reply -- so callers no longer need an explicit
    ``persist_run`` call after ``agent.run``::

        agent = Agent(
            "openai:gpt-4o-mini",
            deps_type=ZepDeps,
            capabilities=zep_capabilities(deps),
        )

    Args:
        deps: The dependency object carrying the client and identity.  Passed
            to :func:`create_zep_after_run_hook`; see its docstring for why
            this is required even though ``ProcessHistory`` reads ``ctx.deps``
            per-request.
        history_processor: The history processor to register via
            ``ProcessHistory``.  Defaults to
            :func:`~zep_pydantic_ai.history_processor.zep_history_processor`;
            override only if you need custom persistence/injection behavior.

    Returns:
        A list of two capabilities: ``ProcessHistory(history_processor)`` and
        ``Hooks(after_run=create_zep_after_run_hook(deps))``.  Pass it
        directly as ``capabilities=zep_capabilities(deps)``.
    """
    return [
        ProcessHistory(history_processor),
        Hooks(after_run=create_zep_after_run_hook(deps)),
    ]
