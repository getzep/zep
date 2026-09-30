"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses every user, thread, and graph by a server-generated UUID. A
create call does not carry an application identifier, and the response carries
the UUID that every later call uses. The application creates the user and the
thread one time, stores ``user.uuid_``, ``user.graph_uuid``, and
``thread.uuid_`` in its own database, and passes those UUIDs to
:class:`zep_pydantic_ai.deps.ZepDeps` on each conversation.

:func:`create_user` and :func:`create_thread` are thin wrappers over the Zep
SDK create methods. Both raise on failure, because provisioning runs before
the first turn and a misconfiguration must be visible there.
"""

from __future__ import annotations

import logging

from zep_cloud.client import AsyncZep
from zep_cloud.types.thread import Thread
from zep_cloud.types.user import User

logger = logging.getLogger(__name__)


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
) -> User:
    """Create a Zep user and return the created user.

    The response carries ``uuid_`` and ``graph_uuid``. Store both values in
    the application database. ``ZepDeps`` takes ``uuid_`` as ``user_uuid``
    and ``graph_uuid`` as ``graph_uuid``.

    Args:
        client: An initialised ``AsyncZep`` client.
        first_name: Optional first name. Zep anchors the user identity node
            with the name, so a real name is strongly recommended.
        last_name: Optional last name.
        email: Optional email. Zep uses the email for identity resolution.

    Returns:
        The created ``User``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx).
    """
    user: User = await client.user.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)
    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    """Create a Zep thread for a user and return the created thread.

    Store ``thread.uuid_`` in the application database. ``ZepDeps`` takes
    the value as ``thread_uuid``.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread.

    Returns:
        The created ``Thread``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx).
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
