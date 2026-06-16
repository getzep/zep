"""
Zep CrewAI Integration.

This package provides comprehensive memory integration between Zep and CrewAI agents,
including graph storage, user storage, and tools for searching and adding data.

Installation:
    pip install zep-crewai

Usage:
    from zep_crewai import ZepGraphStorage, ZepUserStorage, create_search_tool
    from zep_cloud.client import Zep
    from crewai import Agent

    # Initialize Zep client
    zep_client = Zep(api_key="your-api-key")

    # For user-specific storage (standalone Zep adapter: save / search / reset)
    user_storage = ZepUserStorage(client=zep_client, user_id="user123", thread_id="thread123")

    # For generic knowledge graphs
    graph_storage = ZepGraphStorage(client=zep_client, graph_id="knowledge_base")

    # Persist conversation turns and business data
    user_storage.save("Hi there!", metadata={"type": "message", "role": "user"})

    # Create tools to let an agent search and write to Zep
    search_tool = create_search_tool(zep_client, user_id="user123")

    # Create agent with tools
    agent = Agent(
        role="Assistant",
        tools=[search_tool],
    )

Note:
    CrewAI 1.x removed ``crewai.memory.storage.interface.Storage`` and the
    ``ExternalMemory(storage=...)`` wrapper. The ``ZepStorage``, ``ZepUserStorage``,
    and ``ZepGraphStorage`` classes are now standalone, framework-agnostic adapters
    that retain their ``save`` / ``search`` / ``reset`` API. Use the
    ``ZepSearchTool`` / ``ZepAddDataTool`` (CrewAI ``BaseTool`` subclasses) to wire
    Zep into a CrewAI agent's tool list, the supported extension point in 1.x.
"""

__version__ = "1.1.2"
__author__ = "Zep AI"
__description__ = "Zep integration for CrewAI"

from .exceptions import ZepDependencyError

try:
    # Check for required CrewAI dependencies - just test import.
    # crewai 1.x removed the legacy ``crewai.memory.storage.interface.Storage``
    # base class and the ``ExternalMemory`` wrapper. The Zep storage classes are
    # now standalone, framework-agnostic adapters (duck-typed save/search/reset),
    # so the only hard CrewAI dependency we require is ``crewai.tools.BaseTool``.
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
