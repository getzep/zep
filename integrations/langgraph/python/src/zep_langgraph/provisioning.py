"""Explicit, out-of-band provisioning of Zep users and threads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

UserSetupHook = Callable[[AsyncZep, Any], Awaitable[None]]
UserSetupHookSync = Callable[[Zep, Any], None]


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> Any:
    """Create a Zep user and return the server-generated user resource."""
    user = await client.user.create(first_name=first_name, last_name=last_name, email=email)
    logger.info("Created Zep user")
    if on_created is not None:
        await on_created(client, user)
    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Any:
    """Create a Zep thread for a user UUID and return the thread resource."""
    thread = await client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread")
    return thread


def create_user_sync(
    client: Zep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHookSync | None = None,
) -> Any:
    """Create a Zep user with a synchronous client."""
    user = client.user.create(first_name=first_name, last_name=last_name, email=email)
    logger.info("Created Zep user")
    if on_created is not None:
        on_created(client, user)
    return user


def create_thread_sync(client: Zep, *, user_uuid: str) -> Any:
    """Create a Zep thread with a synchronous client."""
    thread = client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread")
    return thread
