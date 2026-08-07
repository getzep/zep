"""
Zep Strands Agents Integration.

This package provides a :class:`~strands.memory.types.MemoryStore` backed by
`Zep <https://www.getzep.com>`_'s temporal Context Graph, for use with the
`Strands Agents <https://strandsagents.com>`_ ``MemoryManager``.

Installation::

    pip install zep-strands

Usage::

    from strands import Agent
    from strands.memory import MemoryManager
    from zep_cloud.client import AsyncZep
    from zep_strands import ZepMemoryStore

    zep = AsyncZep(api_key="your-api-key")
    store = ZepMemoryStore(
        zep_client=zep,
        user_id="user-123",
        thread_id="thread-abc",
        first_name="Jane",
        last_name="Smith",
        writable=True,
        extraction=True,
    )
    agent = Agent(memory_manager=MemoryManager(stores=[store]))
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Strands Agents memory-store integration for Zep"

from .exceptions import ZepDependencyError

# Guard ONLY the Strands import here. A failure importing the integration's
# own modules (e.g. a broken ``zep_cloud``) must surface as its own error
# rather than being mislabeled as a missing Strands dependency.
try:
    import strands  # noqa: F401
except ImportError as e:
    raise ZepDependencyError(
        framework="Strands Agents",
        install_command="pip install zep-strands",
    ) from e

from .memory_store import (
    ADD_TYPE_METADATA_KEY,
    DEFAULT_MAX_SEARCH_RESULTS,
    DEFAULT_STORE_DESCRIPTION,
    DEFAULT_STORE_NAME,
    ZepMemoryStore,
)
from .provisioning import UserSetupHook, ensure_thread, ensure_user
from .search import (
    Reranker,
    Scope,
    ZepSearchTool,
    create_zep_search_tool,
)

__all__ = [
    "ADD_TYPE_METADATA_KEY",
    "DEFAULT_MAX_SEARCH_RESULTS",
    "DEFAULT_STORE_DESCRIPTION",
    "DEFAULT_STORE_NAME",
    "Reranker",
    "Scope",
    "UserSetupHook",
    "ZepDependencyError",
    "ZepMemoryStore",
    "ZepSearchTool",
    "create_zep_search_tool",
    "ensure_user",
    "ensure_thread",
]
