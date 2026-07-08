"""
Zep Memory integration for LiveKit.

This module provides a memory-enabled agent that integrates Zep with LiveKit's voice AI framework.
"""

from .agent import (
    DEFAULT_CONTEXT_TEMPLATE,
    ContextBuilder,
    ContextInput,
    GraphContextBuilder,
    GraphContextInput,
    ZepGraphAgent,
    ZepUserAgent,
)
from .exceptions import AgentConfigurationError, ZepLiveKitError
from .provisioning import UserSetupHook, ensure_thread, ensure_user
from .tools import create_graph_search_tool

__version__ = "0.2.0"
__all__ = [
    "ZepUserAgent",
    "ZepGraphAgent",
    "ZepLiveKitError",
    "AgentConfigurationError",
    "ensure_user",
    "ensure_thread",
    "UserSetupHook",
    "ContextBuilder",
    "ContextInput",
    "GraphContextBuilder",
    "GraphContextInput",
    "DEFAULT_CONTEXT_TEMPLATE",
    "create_graph_search_tool",
]
