"""
Exception classes for AutoGen integration.
"""


class ZepDependencyError(ImportError):
    """Raised when required AutoGen dependencies are not installed."""

    def __init__(self, framework: str, install_command: str):
        self.framework = framework
        self.install_command = install_command
        super().__init__(f"{framework} dependencies not found. Install with: {install_command}")
