"""
Zep CrewAI Integration.

This package provides memory integration between Zep and CrewAI agents,
enabling persistent conversation memory and context retrieval.

Installation:
    pip install zep-crewai

Usage:
    from zep_crewai import ZepStorage
    from zep_cloud.client import AsyncZep
    from crewai.memory.external.external_memory import ExternalMemory
    from crewai import Crew

    # Initialize Zep client and storage
    zep_client = AsyncZep(api_key="your-api-key")
    zep_storage = ZepStorage(client=zep_client, user_id="user123", thread_id="thread123")
    external_memory = ExternalMemory(storage=zep_storage)

    # Create crew with Zep memory
    crew = Crew(
        agents=[...],
        tasks=[...],
        external_memory=external_memory
    )
"""

__version__ = "0.1.0"
__author__ = "Zep AI"
__description__ = "Zep integration for CrewAI"

from .exceptions import ZepDependencyError

try:
    # Check for required CrewAI dependencies - just test import
    import crewai.memory.storage.interface  # noqa: F401

    # Import our integration
    from .memory import ZepStorage

    __all__ = ["ZepStorage", "ZepDependencyError"]

except ImportError as e:
    raise ZepDependencyError(framework="CrewAI", install_command="pip install zep-crewai") from e
