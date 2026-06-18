"""
Zep LangGraph Integration.

This package gives `LangGraph <https://github.com/langchain-ai/langgraph>`_ agents
durable, cross-session memory backed by `Zep <https://www.getzep.com>`_'s temporal
Context Graph.

Two layers are provided:

**Primary -- node / tool helpers** (the recommended path, matching Zep's own
LangGraph guide). Call the Zep client directly inside your graph nodes:

* :func:`~zep_langgraph.context.build_system_message` / :func:`~zep_langgraph.context.get_zep_context`
  -- fetch the user's Context Block (from :meth:`thread.get_user_context`) and
  inject it into the system prompt.
* :func:`~zep_langgraph.persistence.persist_messages` -- persist a conversation
  turn (wraps :meth:`thread.add_messages`).
* :func:`~zep_langgraph.tools.create_graph_search_tool` -- a prebuilt
  LangChain/LangGraph tool over :meth:`graph.search`, ready for
  ``create_react_agent``.

**Secondary -- ``ZepStore``** (:class:`~zep_langgraph.store.ZepStore`), a
hybrid-delegate :class:`~langgraph.store.base.BaseStore` for the
langmem / ``create_react_agent(store=...)`` audience. It wraps a backing KV store
for exact-key operations and routes semantic ``search`` to Zep.

Installation::

    pip install zep-langgraph

Quick start (primary path)::

    from zep_cloud.client import AsyncZep
    from zep_langgraph import build_system_message, persist_messages, create_graph_search_tool

    zep = AsyncZep(api_key="your-api-key")

    async def agent_node(state):
        system = await build_system_message(
            zep, thread_id=state["thread_id"],
            base_instructions="You are a helpful assistant.",
        )
        response = await llm.ainvoke([system, *state["messages"]])
        await persist_messages(
            zep, thread_id=state["thread_id"],
            messages=[state["messages"][-1], response],
            user_name="Alice Smith",
        )
        return {"messages": [response]}
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "LangGraph integration for Zep"

from .exceptions import ZepDependencyError

try:
    # Verify required LangGraph / LangChain dependencies are importable.
    import langchain_core.tools  # noqa: F401
    import langgraph.store.base  # noqa: F401

    from .context import (
        DEFAULT_CONTEXT_TEMPLATE,
        build_system_message,
        build_system_message_sync,
        format_context_block,
        get_zep_context,
        get_zep_context_sync,
    )
    from .persistence import (
        MAX_MESSAGE_CHARS,
        MAX_MESSAGES_PER_CALL,
        persist_messages,
        persist_messages_sync,
        to_zep_message,
        to_zep_messages,
    )
    from .store import NamespaceTargetResolver, ZepStore
    from .tools import (
        DEFAULT_TOOL_DESCRIPTION,
        DEFAULT_TOOL_NAME,
        GraphSearchReranker,
        GraphSearchScope,
        create_graph_search_tool,
        create_graph_search_tool_sync,
    )

    __all__ = [
        # context
        "build_system_message",
        "build_system_message_sync",
        "get_zep_context",
        "get_zep_context_sync",
        "format_context_block",
        "DEFAULT_CONTEXT_TEMPLATE",
        # persistence
        "persist_messages",
        "persist_messages_sync",
        "to_zep_message",
        "to_zep_messages",
        "MAX_MESSAGE_CHARS",
        "MAX_MESSAGES_PER_CALL",
        # tools
        "create_graph_search_tool",
        "create_graph_search_tool_sync",
        "GraphSearchScope",
        "GraphSearchReranker",
        "DEFAULT_TOOL_NAME",
        "DEFAULT_TOOL_DESCRIPTION",
        # store
        "ZepStore",
        "NamespaceTargetResolver",
        # exceptions
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(
        framework="LangGraph",
        install_command="pip install zep-langgraph",
    ) from e
