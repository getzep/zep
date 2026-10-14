"""Create Zep users and threads for Google ADK sessions."""

from __future__ import annotations

from typing import cast

from zep_cloud import Thread, User
from zep_cloud.client import AsyncZep


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
) -> User:
    """Create a Zep user and return its generated UUID."""
    return cast(
        User,
        await client.user.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
        ),
    )


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    """Create a thread for a Zep user and return its generated UUID."""
    return cast(Thread, await client.thread.create(user_uuid=user_uuid))
