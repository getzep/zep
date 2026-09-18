"""
Zep Google ADK Integration.

This package provides memory integration between Zep and Google ADK agents.
It automatically persists conversation messages to Zep and injects relevant
context from Zep's long-term memory into LLM prompts.

The integration uses ADK session state for per-user identity, so a single
Agent definition can be shared across all users -- no per-session factory
needed.

Installation:
    pip install zep-adk

Usage:
    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from zep_cloud.client import AsyncZep
    from zep_adk import ZepContextTool, create_after_model_callback, create_user, create_thread

    zep = AsyncZep(api_key="your-api-key")

    # One shared agent definition
    agent = Agent(
        name="my_agent",
        model="gemini-2.5-flash",
        instruction="You are a helpful assistant with long-term memory.",
        tools=[ZepContextTool(zep_client=zep)],
        after_model_callback=create_after_model_callback(zep_client=zep),
    )

    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name="my_app", session_service=session_service)

    # Provision the Zep user and thread out-of-band, before the first turn
    # (e.g. during account/session onboarding).  Zep v4 generates the UUIDs;
    # read them from the responses and store them in your own database.
    user = await create_user(
        zep,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",  # optional
    )
    thread = await create_thread(zep, user_uuid=user.uuid_)

    # Put the UUIDs in ADK session state.  The turn path addresses Zep by UUID.
    await session_service.create_session(
        app_name="my_app",
        user_id="user_123",          # your own application user ID
        session_id="session_abc",    # your own application session ID
        state={
            "zep_user_uuid": user.uuid_,
            "zep_thread_uuid": thread.uuid_,
            "zep_graph_uuid": user.graph_uuid,
            "zep_first_name": "Jane",
            "zep_last_name": "Smith",
        },
    )
"""

__version__ = "0.3.1"
__author__ = "Zep AI"
__description__ = "Google ADK integration for Zep"

from .exceptions import ZepDependencyError

try:
    # Check for required ADK dependencies
    import google.adk.tools.base_tool  # noqa: F401

    # Import our integration components
    from .callbacks import create_after_model_callback
    from .context_tool import (
        DEFAULT_CONTEXT_TEMPLATE,
        ContextBuilder,
        ContextInput,
        ZepContextTool,
    )
    from .graph_search_tool import ZepGraphSearchTool
    from .memory_service import ZepMemoryService
    from .provisioning import create_thread, create_user

    __all__ = [
        "DEFAULT_CONTEXT_TEMPLATE",
        "ContextBuilder",
        "ContextInput",
        "ZepContextTool",
        "ZepGraphSearchTool",
        "ZepMemoryService",
        "create_after_model_callback",
        "create_thread",
        "create_user",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="Google ADK", install_command="pip install zep-adk") from e
