"""
Exception classes for AutoGen integration.
"""

from typing import Any


def reject_removed_kwargs(owner: str, kwargs: dict[str, Any], removed: dict[str, str]) -> None:
    """Reject a removed V3 keyword argument with a named V4 replacement.

    A caller who migrates from the V3 API can pass a removed argument
    through ``**kwargs``. The argument would land in a catch-all dict and be
    silently ignored, so the class would run with wrong configuration (for
    example a silently auto-created thread in place of the one the caller
    named). This check fails loudly instead: a removed name raises a
    ``TypeError`` that names the V4 replacement, and any other unexpected
    name raises a standard unexpected-argument ``TypeError``.

    Args:
        owner: The name of the class for the error message.
        kwargs: The unexpected keyword arguments the constructor received.
        removed: A map of a removed argument name to the text that names
            its replacement.

    Raises:
        TypeError: Always, when ``kwargs`` is not empty.
    """
    for name, replacement in removed.items():
        if name in kwargs:
            raise TypeError(f"`{name}` was removed; {replacement}")
    if kwargs:
        names = ", ".join(f"'{n}'" for n in sorted(kwargs))
        raise TypeError(f"{owner}() got unexpected keyword arguments: {names}")


class ZepDependencyError(ImportError):
    """Raised when required AutoGen dependencies are not installed."""

    def __init__(self, framework: str, install_command: str):
        self.framework = framework
        self.install_command = install_command
        super().__init__(f"{framework} dependencies not found. Install with: {install_command}")
