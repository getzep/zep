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
    from zep_ms_agent_framework import ZepContextProvider

    zep = AsyncZep(api_key="your-api-key")

    agent = Agent(
        OpenAIChatClient(model="gpt-5-mini"),
        instructions="You are a helpful assistant with long-term memory.",
        context_providers=[
            ZepContextProvider(
                zep_client=zep,
                user_id="user-123",
                thread_id="thread-abc",
                first_name="Jane",
                last_name="Smith",
            )
        ],
    )

    result = await agent.run("Hi, I'm a data scientist in Portland.")
    print(result.text)
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Microsoft Agent Framework integration for Zep"

from .exceptions import ZepDependencyError

try:
    # Verify the Microsoft Agent Framework core is importable before exposing
    # the integration surface.
    import agent_framework  # noqa: F401

    from .context_provider import (
        DEFAULT_SOURCE_ID,
        UserSetupHook,
        ZepContextProvider,
    )

    __all__ = [
        "DEFAULT_SOURCE_ID",
        "UserSetupHook",
        "ZepContextProvider",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(
        framework="Microsoft Agent Framework",
        install_command="pip install zep-ms-agent-framework",
    ) from e
