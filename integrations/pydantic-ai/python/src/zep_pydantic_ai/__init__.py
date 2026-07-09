"""
Zep Pydantic AI Integration.

This package wires Zep's long-term agent memory into `Pydantic AI
<https://ai.pydantic.dev>`_ agents.  It persists conversation turns to Zep and
injects relevant context from Zep's temporal knowledge graph into the model's
prompt on every turn -- using Pydantic AI's native ``ProcessHistory``
capability -- plus an on-demand graph-search tool.

The integration's public pieces:

* :class:`ZepDeps` -- a dataclass carrying the Zep client + user/thread identity.
  Use it as the agent's ``deps_type`` and pass an instance to ``agent.run``.
* :func:`zep_history_processor` -- registered via
  ``capabilities=[ProcessHistory(zep_history_processor)]``; persists the latest
  user turn and prepends Zep's context block.  Set ``ZepDeps.context_builder``
  for custom context retrieval, or ``ZepDeps.context_template`` to customise
  how context is wrapped before injection.
* :func:`zep_capabilities` -- one-line capability wiring that also
  auto-persists the assistant's reply via ``Hooks(after_run=...)``; or pair
  :func:`zep_history_processor` with :func:`persist_run` for explicit control.
* :func:`create_zep_search_tool` -- a factory producing a model-callable
  ``pydantic_ai.Tool`` over ``graph.search``, with pin-or-expose control over
  which search parameters the model can set.
* :func:`ensure_user` / :func:`ensure_thread` -- explicit, out-of-band
  provisioning helpers for onboarding flows that want genuine failures to
  raise loudly, before the first turn.

Installation::

    pip install zep-pydantic-ai

Usage::

    from pydantic_ai import Agent
    from zep_cloud.client import AsyncZep
    from zep_pydantic_ai import ZepDeps, create_zep_search_tool, zep_capabilities

    zep = AsyncZep(api_key="your-api-key")

    deps = ZepDeps(
        client=zep,
        user_id="user_123",
        thread_id="thread_abc",
        first_name="Jane",
        last_name="Smith",
    )

    agent = Agent(
        "openai:gpt-4o-mini",
        deps_type=ZepDeps,
        capabilities=zep_capabilities(deps),
        tools=[create_zep_search_tool()],
        instructions="You are a helpful assistant with long-term memory.",
    )

    result = await agent.run("What did I tell you about my project?", deps=deps)
    # The user turn and the assistant's reply are both already persisted.
"""

from .exceptions import ZepDependencyError

__version__ = "0.2.0"
__author__ = "Zep AI"
__description__ = "Pydantic AI integration for Zep"

try:
    import pydantic_ai.capabilities  # noqa: F401

    from .capabilities import create_zep_after_run_hook, zep_capabilities
    from .deps import (
        DEFAULT_CONTEXT_TEMPLATE,
        ContextBuilder,
        ContextInput,
        ZepDeps,
    )
    from .history_processor import (
        HistoryProcessorFn,
        persist_run,
        reset_turn_cache,
        zep_history_processor,
    )
    from .provisioning import UserSetupHook, ensure_thread, ensure_user
    from .search import (
        Reranker,
        Scope,
        ZepSearchTool,
        create_zep_search_tool,
    )

    __all__ = [
        "ZepDeps",
        "zep_history_processor",
        "persist_run",
        "reset_turn_cache",
        "HistoryProcessorFn",
        "create_zep_search_tool",
        "ZepSearchTool",
        "Scope",
        "Reranker",
        "ZepDependencyError",
        "ensure_user",
        "ensure_thread",
        "UserSetupHook",
        "ContextInput",
        "ContextBuilder",
        "DEFAULT_CONTEXT_TEMPLATE",
        "create_zep_after_run_hook",
        "zep_capabilities",
    ]

except ImportError as e:
    raise ZepDependencyError(
        framework="Pydantic AI", install_command="pip install zep-pydantic-ai"
    ) from e
