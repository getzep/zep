"""
Zep Microsoft Agent Framework Integration.

This package provides long-term memory for `Microsoft Agent Framework
<https://github.com/microsoft/agent-framework>`_ agents, backed by `Zep
<https://www.getzep.com>`_'s temporal Context Graph.

It ships a single :class:`~agent_framework.ContextProvider` --
:class:`ZepContextProvider` -- that persists conversation turns to a user's Zep
thread and injects Zep's Context Block into the model's instructions on every
run.  Attach it to an agent through the ``context_providers`` keyword argument
and a single agent definition gains durable, cross-session memory.

Installation::

    pip install zep-ms-agent-framework

Usage::

    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatClient
    from zep_cloud.client import AsyncZep
    from zep_ms_agent_framework import ZepContextProvider, create_thread, create_user

    zep = AsyncZep(api_key="your-api-key")

    # Create the user and the thread one time, and store the UUIDs.
    user = await create_user(zep, first_name="Jane", last_name="Smith")
    thread = await create_thread(zep, user_uuid=user.uuid_)

    agent = Agent(
        OpenAIChatClient(model="gpt-5-mini"),
        instructions="You are a helpful assistant with long-term memory.",
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_uuid=user.uuid_,
                thread_uuid=thread.uuid_,
                graph_uuid=user.graph_uuid,
            )
        ],
    )

    result = await agent.run("Hi, I'm a data scientist in Portland.")
    print(result.text)
"""

__version__ = "0.2.1"
__author__ = "Zep AI"
__description__ = "Microsoft Agent Framework integration for Zep"

from .exceptions import ZepDependencyError

# Guard ONLY the Microsoft Agent Framework import here.  A failure importing the
# integration's own modules (e.g. a broken ``zep_cloud``) must surface as its own
# error rather than being mislabeled as a missing Agent Framework dependency.
try:
    import agent_framework  # noqa: F401
except ImportError as e:
    raise ZepDependencyError(
        framework="Microsoft Agent Framework",
        install_command="pip install zep-ms-agent-framework",
    ) from e

from .context_provider import (
    DEFAULT_CONTEXT_TEMPLATE,
    DEFAULT_SOURCE_ID,
    ContextBuilder,
    ContextInput,
    ZepContextProvider,
)
from .provisioning import UserSetupHook, create_thread, create_user
from .search import (
    Reranker,
    Scope,
    ZepSearchTool,
    create_zep_search_tool,
)

__all__ = [
    "DEFAULT_CONTEXT_TEMPLATE",
    "DEFAULT_SOURCE_ID",
    "ContextBuilder",
    "ContextInput",
    "Reranker",
    "Scope",
    "UserSetupHook",
    "ZepContextProvider",
    "ZepDependencyError",
    "ZepSearchTool",
    "create_thread",
    "create_user",
    "create_zep_search_tool",
]
