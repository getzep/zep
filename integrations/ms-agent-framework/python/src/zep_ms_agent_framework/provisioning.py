"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
``user_id`` or a ``thread_id`` is a name, not an address. A create call returns
the new resource, and the application stores its ``uuid_`` for all later calls.

``ZepContextProvider`` therefore does not create Zep resources. Create the user
and the thread out-of-band, before the first run, and give the provider the
UUIDs. These helpers do that, and they return the created resource so the
caller can read ``uuid_`` and ``graph_uuid``.

Both helpers let a failure propagate. Out-of-band provisioning must fail
loudly, so a misconfiguration is found before the agent runs.
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

    The returned :class:`~zep_cloud.types.User` carries ``uuid_``, the address
    of the user in v4, and ``graph_uuid``, the address of the user's graph.
    Store both values in your own database. ``ZepContextProvider`` takes
    ``user_uuid``, and the graph-search tool takes ``graph_uuid``.

    Every call creates a new user. Zep v4 has no create-if-absent call, so
    this helper is not idempotent.

    When ``on_created`` is given, the hook is awaited with ``(client,
    user.uuid_)`` before this function returns. If the hook raises, the
    exception propagates to the caller although the user was created.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: Optional name for the user. Zep does not use it as an
            address, and it does not need to be unique for a create call to
            succeed.
        first_name: Optional first name. Real names help Zep resolve the
            identity of the user in the graph.
        last_name: Optional last name.
        email: Optional email address.
        on_created: Optional async hook run one time, after the user is
            created.

    Returns:
        The created ``User``.

    Raises:
        Exception: Any failure from the Zep SDK, or any exception that
            ``on_created`` raises.
    """
    # The v4 SDK leaves its sub-client properties unannotated, so ``client.user``
    # is ``Any`` to a type checker.  The annotation restores the static type.
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
    client: AsyncZep,
    *,
    user_uuid: str,
    thread_id: str | None = None,
) -> Thread:
    """Create a Zep thread for a user and return it.

    The returned :class:`~zep_cloud.types.Thread` carries ``uuid_``, the
    address of the thread in v4. Store it, and give it to
    ``ZepContextProvider`` as ``thread_uuid``.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread.
        thread_id: Optional name for the thread. Zep does not use it as an
            address.

    Returns:
        The created ``Thread``.

    Raises:
        Exception: Any failure from the Zep SDK.
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
