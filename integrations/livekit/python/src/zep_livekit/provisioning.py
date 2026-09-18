"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
A ``user_id`` or a ``thread_id`` is a name, not an address, so an
integration cannot resolve a resource by name on the hot path. Create the
resources one time with :func:`create_user` and :func:`create_thread`, store
the returned UUIDs in your own database, and pass the stored UUIDs to
``ZepUserAgent``.

These helpers raise on failure. Call them during account or session
onboarding, before the first turn, so misconfiguration is loud.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the UUID of the new user.  Use this to configure
#: per-user ontology, custom instructions, or user summary instructions.
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]


async def create_user(
    client: AsyncZep,
    *,
    user_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> User:
    """Create a Zep user and return it.

    The returned :class:`~zep_cloud.types.user.User` carries the two UUIDs
    that the integration needs: ``uuid_`` addresses the user, and
    ``graph_uuid`` addresses the graph of the user. Store both in your own
    database.

    When ``on_created`` is provided, the hook is awaited (with ``(client,
    user_uuid)``) **before** this function returns. If the hook raises, the
    exception propagates to the caller even though the user was created.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: Optional name for the user. A name is not an address, and
            v4 does not require one. Set it only when your application
            already has an identifier that a person must recognise in the
            Zep dashboard.
        first_name: Optional first name.
        last_name: Optional last name.
        email: Optional email.
        on_created: Optional async hook, awaited with the UUID of the new
            user.

    Returns:
        The created ``User``.

    Raises:
        Exception: Any failure from the Zep SDK, or any exception raised by
            ``on_created``.
    """
    # The v4 SDK exposes each sub-client through an unannotated property, so
    # the result is untyped. The annotation restores the type.
    user: User = await client.user.create(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None and user.uuid_ is not None:
        await on_created(client, user.uuid_)
        logger.info("on_created hook completed for user %s", user.uuid_)

    return user


async def create_thread(
    client: AsyncZep, *, user_uuid: str, thread_id: str | None = None
) -> Thread:
    """Create a Zep thread for a user and return it.

    Read ``uuid_`` from the returned :class:`~zep_cloud.types.thread.Thread`
    and store it. ``ZepUserAgent`` takes that UUID as ``thread_uuid``.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread.
        thread_id: Optional name for the thread. A name is not an address.

    Returns:
        The created ``Thread``.

    Raises:
        Exception: Any failure from the Zep SDK.
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
