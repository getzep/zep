"""
Zep Pydantic AI Integration.

This package wires Zep's long-term agent memory into `Pydantic AI
<https://ai.pydantic.dev>`_ agents.  It persists conversation turns to Zep and
injects relevant context from Zep's temporal knowledge graph into the model's
prompt on every turn -- using Pydantic AI's native ``ProcessHistory``
capability -- plus an on-demand graph-search tool.

The integration has three public pieces:

* :class:`ZepDeps` -- a dataclass carrying the Zep client + user/thread identity.
  Use it as the agent's ``deps_type`` and pass an instance to ``agent.run``.
* :func:`zep_history_processor` -- registered via
  ``capabilities=[ProcessHistory(zep_history_processor)]``; persists the latest
  user turn and prepends Zep's context block.  Pair it with :func:`persist_run`
  to store the assistant's reply after the run.
* :func:`create_zep_search_tool` -- a factory producing a model-callable
  ``@agent.tool`` over ``graph.search``.

Installation::

    pip install zep-pydantic-ai

Usage::

    from pydantic_ai import Agent
    from pydantic_ai.capabilities import ProcessHistory
    from zep_cloud.client import AsyncZep
    from zep_pydantic_ai import (
        ZepDeps,
        zep_history_processor,
        create_zep_search_tool,
        persist_run,
    )

    zep = AsyncZep(api_key="your-api-key")

    agent = Agent(
        "openai:gpt-4o-mini",
        deps_type=ZepDeps,
        capabilities=[ProcessHistory(zep_history_processor)],
        tools=[create_zep_search_tool()],
        instructions="You are a helpful assistant with long-term memory.",
    )

    deps = ZepDeps(
        client=zep,
        user_id="user_123",
        thread_id="thread_abc",
        first_name="Jane",
        last_name="Smith",
    )

    result = await agent.run("What did I tell you about my project?", deps=deps)
    await persist_run(deps, result.new_messages())
"""

from .exceptions import ZepDependencyError

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Pydantic AI integration for Zep"

try:
    import pydantic_ai.capabilities  # noqa: F401

    from .deps import ZepDeps
    from .history_processor import (
        HistoryProcessorFn,
        persist_run,
        reset_turn_cache,
        zep_history_processor,
    )
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
    ]

except ImportError as e:
    raise ZepDependencyError(
        framework="Pydantic AI", install_command="pip install zep-pydantic-ai"
    ) from e
