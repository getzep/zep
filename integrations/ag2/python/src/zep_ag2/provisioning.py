"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
``user_id`` or a ``thread_id`` is a name, not an address. The helpers here
create a user and a thread one time, and they return the created resource.
Store ``user.uuid_``, ``user.graph_uuid``, and ``thread.uuid_`` in your own
database, and give those UUIDs to :class:`zep_ag2.ZepMemoryManager` and to the
tool factories.

Both helpers raise on every failure. Out-of-band provisioning is meant to fail
loudly, so a misconfiguration is caught before the agent runs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs one time after a Zep user is
#: created.
#:
#: Receives the Zep client and the UUID of the new user. Use this to configure
#: the ontology, the custom instructions, or the summary instructions of the
#: user.
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

    The response carries the two identifiers that the integration needs:
    ``user.uuid_`` and ``user.graph_uuid``. Store both.

    When ``on_created`` is given, the hook is awaited with
    ``(client, user.uuid_)`` before this function returns. If the hook raises,
    the exception propagates to the caller although the user was created. Make
    the hook idempotent, so that you can run it again against a user whose
    setup only partially completed.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: Optional name for the user. Zep v4 does not need it, and it
            must be unique in the project when it is given.
        first_name: Optional first name.
        last_name: Optional last name.
        email: Optional email address.
        on_created: Optional async hook that runs one time after creation.

    Returns:
        The created ``User``.

    Raises:
        Exception: Any failure from the Zep SDK, or any exception that
            ``on_created`` raises.
    """
    user: User = await client.user.create(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None and user.uuid_:
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

    Store ``thread.uuid_``. The integration addresses the thread by that UUID.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread.
        thread_id: Optional name for the thread. Zep v4 does not need it, and
            it must be unique in the project when it is given.

    Returns:
        The created ``Thread``.

    Raises:
        Exception: Any failure from the Zep SDK.
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
