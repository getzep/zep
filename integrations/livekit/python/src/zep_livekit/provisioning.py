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
    user = cast(
        User,
        await client.user.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
        ),
    )
    if on_created is not None:
        await on_created(client, cast(str, user.uuid_))
    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    return cast(Thread, await client.thread.create(user_uuid=user_uuid))
