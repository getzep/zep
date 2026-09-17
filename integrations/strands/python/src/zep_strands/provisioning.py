"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID. A
``user_id`` or a ``thread_id`` is a name, not an address. The application
creates the user and the thread one time, stores the returned UUIDs in its own
database, and gives those UUIDs to :class:`~zep_strands.memory_store.ZepMemoryStore`.

:func:`create_user` and :func:`create_thread` do the creation. They return the
created object, so the caller can read ``uuid_`` from the response. The store
never creates a resource, and it never resolves a name at run time.

A failure always propagates. Out-of-band provisioning is meant to fail loudly,
so that a misconfiguration is found before the agent runs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs one time after a Zep user is created.
#:
#: The hook receives the Zep client and the UUID of the new user. Use the hook
#: to configure a per-user ontology, custom instructions, or user summary
#: instructions.
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

    Store ``user.uuid_`` in your own database and pass it to the store as
    ``user_uuid``. The UUID is the address of the user in v4.

    When ``on_created`` is provided, the hook is awaited with
    ``(client, user_uuid)`` before this function returns. If the hook raises,
    the exception propagates although the user was created. Make the hook
    idempotent, so that you can run it again after a failure.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: Optional name for the user. The name is a label. It is not an
            address, and Zep rejects a name that is already in use.
        first_name: Optional first name.
        last_name: Optional last name.
        email: Optional email address.
        on_created: Optional async hook that runs one time after creation.

    Returns:
        The created ``User``. Read ``uuid_`` from it.

    Raises:
        Exception: Any failure from the Zep SDK, or any exception from
            ``on_created``.
    """
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

    Store ``thread.uuid_`` in your own database and pass it to the store as
    ``thread_uuid``.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread.
        thread_id: Optional name for the thread. The name is a label, not an
            address.

    Returns:
        The created ``Thread``. Read ``uuid_`` from it.

    Raises:
        Exception: Any failure from the Zep SDK.
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
