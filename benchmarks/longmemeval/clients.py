#!/usr/bin/env python3
"""
Client factory functions for external services
"""

import os

from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep


def create_zep_client(api_key: str | None = None) -> AsyncZep:
    """
    Create and configure a Zep client

    Args:
        api_key: Zep API key (defaults to ZEP_API_KEY env var)

    Returns:
        Configured AsyncZep client
    """
    load_dotenv()

    key = api_key or os.getenv("ZEP_API_KEY")
    if not key:
        raise ValueError("ZEP_API_KEY must be provided or set in environment")

    return AsyncZep(api_key=key)


def create_openai_client(api_key: str | None = None) -> AsyncOpenAI:
    """
    Create and configure an OpenAI client

    Args:
        api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)

    Returns:
        Configured AsyncOpenAI client
    """
    load_dotenv()

    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY must be provided or set in environment")

    return AsyncOpenAI(api_key=key)
