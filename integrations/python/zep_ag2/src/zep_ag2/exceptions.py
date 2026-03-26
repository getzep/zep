"""
Exception classes for zep-ag2 integration.
"""


class ZepAG2Error(Exception):
    """Base exception for zep-ag2 package."""

    pass


class ZepAG2ConfigError(ZepAG2Error):
    """Raised when configuration is invalid."""

    pass


class ZepAG2MemoryError(ZepAG2Error):
    """Raised when memory operations fail."""

    pass


class ZepDependencyError(ImportError):
    """Raised when required AG2 dependencies are not installed."""

    def __init__(self, framework: str, install_command: str):
        self.framework = framework
        self.install_command = install_command
        super().__init__(f"{framework} dependencies not found. Install with: {install_command}")
