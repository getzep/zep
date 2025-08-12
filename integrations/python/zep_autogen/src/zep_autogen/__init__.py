"""
Zep AutoGen Integration.

This package provides memory integration between Zep and Microsoft AutoGen agents,
enabling persistent conversation memory and context retrieval.

Installation:
    pip install zep-autogen

Usage:
    from zep_autogen import ZepMemory
    from zep_cloud.client import AsyncZep
    from autogen_agentchat.agents import AssistantAgent

    # Initialize Zep client and memory
    zep_client = AsyncZep(api_key="your-api-key")
    memory = ZepMemory(client=zep_client, user_id="user123")

    # Create agent with Zep memory
    agent = AssistantAgent(
        name="assistant",
        model_client=model_client,
        memory=[memory]
    )
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Zep integration for Microsoft AutoGen"

from .exceptions import ZepDependencyError

try:
    # Check for required AutoGen dependencies - just test import
    import autogen_core.memory  # noqa: F401
    import autogen_core.model_context  # noqa: F401

    from .graph_memory import ZepGraphMemory

    # Import our integration
    from .memory import ZepUserMemory
    from .tools import create_add_graph_data_tool, create_search_graph_tool

    __all__ = [
        "ZepUserMemory",
        "ZepGraphMemory",
        "create_search_graph_tool",
        "create_add_graph_data_tool",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="AutoGen", install_command="pip install zep-autogen") from e
