"""
LiveKit-specific exceptions for Zep integration.
"""


class ZepLiveKitError(Exception):
    """Base exception for Zep LiveKit integration errors."""

    pass


class AgentConfigurationError(ZepLiveKitError):
    """Raised when agent configuration is invalid."""

    pass


class MemoryStorageError(ZepLiveKitError):
    """Raised when memory storage operations fail."""

    pass


class MemoryRetrievalError(ZepLiveKitError):
    """Raised when memory retrieval operations fail."""

    pass