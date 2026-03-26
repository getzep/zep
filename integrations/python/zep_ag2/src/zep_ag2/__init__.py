"""
Zep memory integration for AG2 (AutoGen community fork).

This package provides tools and helpers for integrating Zep's long-term memory
and knowledge graph capabilities with AG2 agents.
"""

from zep_ag2.exceptions import (
    ZepAG2ConfigError,
    ZepAG2Error,
    ZepAG2MemoryError,
    ZepDependencyError,
)

try:
    from zep_ag2.graph_memory import ZepGraphMemoryManager
    from zep_ag2.memory import ZepMemoryManager
    from zep_ag2.tools import (
        create_add_graph_data_tool,
        create_add_memory_tool,
        create_search_graph_tool,
        create_search_memory_tool,
        register_all_tools,
    )

    __all__ = [
        "ZepMemoryManager",
        "ZepGraphMemoryManager",
        "create_search_memory_tool",
        "create_add_memory_tool",
        "create_search_graph_tool",
        "create_add_graph_data_tool",
        "register_all_tools",
        "ZepAG2Error",
        "ZepAG2ConfigError",
        "ZepAG2MemoryError",
        "ZepDependencyError",
    ]

except ImportError as e:
    raise ZepDependencyError(framework="AG2", install_command="pip install zep-ag2") from e

__version__ = "0.1.0"
