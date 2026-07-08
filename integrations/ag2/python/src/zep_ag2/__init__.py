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

# Verify the AG2 runtime dependency is actually importable. AG2 is distributed
# as `ag2` on PyPI but imported as `autogen`. Users register the tools and
# managers onto AG2 agents, so a missing AG2 install should fail with a clear,
# truthful error rather than an opaque ImportError later.
try:
    import autogen  # noqa: F401
except ImportError as e:
    raise ZepDependencyError(framework="AG2", install_command="pip install zep-ag2") from e

from zep_ag2.graph_memory import ZepGraphMemoryManager
from zep_ag2.memory import (
    DEFAULT_CONTEXT_TEMPLATE,
    ContextBuilder,
    ContextInput,
    ZepMemoryManager,
)
from zep_ag2.provisioning import UserSetupHook, ensure_thread, ensure_user
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
    "ensure_user",
    "ensure_thread",
    "UserSetupHook",
    "ContextBuilder",
    "ContextInput",
    "DEFAULT_CONTEXT_TEMPLATE",
]

__version__ = "0.2.0"
