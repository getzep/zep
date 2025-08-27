"""
Zep Memory integration for LiveKit.

This module provides a memory-enabled agent that integrates Zep with LiveKit's voice AI framework.
"""

from .agent import ZepGraphAgent, ZepUserAgent
from .exceptions import ZepLiveKitError

__version__ = "0.1.0"
__all__ = ["ZepUserAgent", "ZepGraphAgent", "ZepLiveKitError"]
