"""Provision Zep users and threads before a crew run."""

from __future__ import annotations

from collections.abc import Callable

from zep_cloud.client import Zep
from zep_cloud.types import Thread, User

UserSetupHook = Callable[[Zep, User], None]


def ensure_user(
    client: Zep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> tuple[User, bool]:
    """Create a user and run the optional setup hook."""
    user = client.user.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    if on_created is not None:
        on_created(client, user)
    return user, True


def ensure_thread(client: Zep, *, user_uuid: str) -> tuple[Thread, bool]:
    """Create a thread for a user."""
    return client.thread.create(user_uuid=user_uuid), True
