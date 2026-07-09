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
    from zep_adk import ZepContextTool, create_after_model_callback, ensure_user, ensure_thread

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
    # (e.g. during account/session onboarding).
    await ensure_user(
        zep,
        user_id="user_123",
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",  # optional
    )
    await ensure_thread(zep, thread_id="session_abc", user_id="user_123")

    # Per-user session: user_id → Zep user, session_id → Zep thread
    await session_service.create_session(
        app_name="my_app",
        user_id="user_123",          # automatically used as Zep user ID
        session_id="session_abc",    # automatically used as Zep thread ID
        state={
            "zep_first_name": "Jane",
            "zep_last_name": "Smith",
        },
    )
"""

__version__ = "0.3.0"
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
    from .provisioning import UserSetupHook, ensure_thread, ensure_user

    __all__ = [
        "DEFAULT_CONTEXT_TEMPLATE",
        "ContextBuilder",
        "ContextInput",
        "UserSetupHook",
        "ZepContextTool",
        "ZepGraphSearchTool",
        "ZepMemoryService",
        "create_after_model_callback",
        "ensure_thread",
        "ensure_user",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="Google ADK", install_command="pip install zep-adk") from e
