"""
Zep Memory integration for LiveKit.

This module provides memory-enabled agents that integrate Zep with LiveKit's voice AI framework.
"""

from .agent import ZepMemoryAgent
from .session import ZepAgentSession
from .exceptions import ZepLiveKitError

__version__ = "0.1.0"
__all__ = ["ZepMemoryAgent", "ZepAgentSession", "ZepLiveKitError"]