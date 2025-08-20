"""
Zep CrewAI Integration.

This package provides comprehensive memory integration between Zep and CrewAI agents,
including graph storage, user storage, and tools for searching and adding data.

Installation:
    pip install zep-crewai

Usage:
    from zep_crewai import ZepGraphStorage, ZepUserStorage, create_search_tool
    from zep_cloud.client import Zep
    from crewai.memory.external.external_memory import ExternalMemory
    from crewai import Agent, Crew

    # Initialize Zep client
    zep_client = Zep(api_key="your-api-key")

    # For user-specific storage
    user_storage = ZepUserStorage(client=zep_client, user_id="user123", thread_id="thread123")

    # For generic knowledge graphs
    graph_storage = ZepGraphStorage(client=zep_client, graph_id="knowledge_base")

    # Create tools
    search_tool = create_search_tool(zep_client, user_id="user123")

    # Create agent with tools
    agent = Agent(
        role="Assistant",
        tools=[search_tool]
    )
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Zep integration for CrewAI"

from .exceptions import ZepDependencyError

try:
    # Check for required CrewAI dependencies - just test import
    import crewai.memory.storage.interface  # noqa: F401
    import crewai.tools  # noqa: F401

    # Import our integration components
    from .graph_storage import ZepGraphStorage
    from .memory import ZepStorage
    from .tools import (
        ZepAddDataTool,
        ZepSearchTool,
        create_add_data_tool,
        create_search_tool,
    )
    from .user_storage import ZepUserStorage

    __all__ = [
        "ZepStorage",
        "ZepGraphStorage",
        "ZepUserStorage",
        "ZepSearchTool",
        "ZepAddDataTool",
        "create_search_tool",
        "create_add_data_tool",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="CrewAI", install_command="pip install zep-crewai") from e
