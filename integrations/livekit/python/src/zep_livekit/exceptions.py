"""
LiveKit-specific exceptions for Zep integration.
"""


class ZepLiveKitError(Exception):
    """Base exception for Zep LiveKit integration errors."""

    pass


class AgentConfigurationError(ZepLiveKitError):
    """Raised when agent configuration is invalid."""

    pass
