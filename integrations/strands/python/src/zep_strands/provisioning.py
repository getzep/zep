"""Create Zep users and threads before an agent run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> User:
    """Create a user and return the server-generated user UUID."""
    user = cast(
        User,
        await client.user.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
        ),
    )
    if on_created is not None:
        if user.uuid_ is None:
            raise RuntimeError("Zep created a user without a UUID")
        await on_created(client, user.uuid_)
    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    """Create a thread for a user and return the server-generated thread UUID."""
    return cast(Thread, await client.thread.create(user_uuid=user_uuid))
