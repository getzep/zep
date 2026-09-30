"""
Explicit, out-of-band Zep resource provisioning.

The LangGraph node-helper path (:func:`~zep_langgraph.context.get_zep_context` /
:func:`~zep_langgraph.persistence.persist_messages`) never creates Zep users or
threads -- it only persists messages and retrieves context. Callers are
expected to provision the Zep user and thread once, out-of-band, before the
first turn (e.g. during account/session onboarding), using :func:`create_user`
and :func:`create_thread` (or their synchronous twins, :func:`create_user_sync`
and :func:`create_thread_sync`, for use with the synchronous ``Zep`` client).

Zep v4 assigns a server-generated UUID to every user, thread, and graph, and a
create call does not take an application identifier. Each helper returns the
created object. Store ``User.uuid_``, ``User.graph_uuid``, and
``Thread.uuid_`` in your own database, and pass the stored UUIDs to the node
helpers on every turn.

Because a v4 create call never collides with an existing application
identifier, these helpers create unconditionally and do not treat any error as
success. Every failure (auth, network, 5xx) raises -- out-of-band provisioning
is meant to fail loudly so misconfiguration is caught before the agent ever
runs, not swallowed into a silent no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from zep_cloud import Thread, User
from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the created :class:`~zep_cloud.types.user.User`.
#: Use this to configure per-user ontology, custom instructions, or user
#: summary instructions against ``user.graph_uuid``.
UserSetupHook = Callable[[AsyncZep, User], Awaitable[None]]

#: Synchronous twin of :data:`UserSetupHook`, for use with :func:`create_user_sync`.
UserSetupHookSync = Callable[[Zep, User], None]


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> User:
    """Create a Zep user and return it.

    Calls ``client.user.create(...)``. Zep assigns the user UUID and the UUID
    of the user's personal graph; the response carries both. Store them
    yourself, because the node helpers address the user's graph by
    ``User.graph_uuid`` and never resolve an application identifier at run
    time.

    When ``on_created`` is provided, the hook is awaited (with
    ``(client, user)``) **before** this function returns. If the hook raises,
    the exception propagates to the caller even though the user was created.
    Re-run the hook logic directly against the user to recover, so make the
    hook idempotent.

    Args:
        client: An initialised ``AsyncZep`` client.
        first_name: Optional first name, passed through to ``user.create``.
        last_name: Optional last name, passed through to ``user.create``.
        email: Optional email, passed through to ``user.create``.
        on_created: Optional async hook run once, after the user is created.

    Returns:
        The created :class:`~zep_cloud.types.user.User`, including ``uuid_``
        and ``graph_uuid``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx), or any
            exception raised by ``on_created``.
    """
    user: User = await client.user.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None:
        await on_created(client, user)
        logger.info("on_created hook completed for user %s", user.uuid_)

    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    """Create a Zep thread for a user and return it.

    Calls ``client.thread.create(...)``. Zep assigns the thread UUID; store
    ``Thread.uuid_`` and pass it to
    :func:`~zep_langgraph.persistence.persist_messages` and
    :func:`~zep_langgraph.context.get_zep_context`.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the Zep user that owns the thread. The user
            must already exist (see :func:`create_user`).

    Returns:
        The created :class:`~zep_cloud.types.thread.Thread`, including
        ``uuid_``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx).
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread


def create_user_sync(
    client: Zep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHookSync | None = None,
) -> User:
    """Synchronous variant of :func:`create_user`.

    Uses a synchronous ``Zep`` client and a synchronous ``on_created`` hook
    (:data:`UserSetupHookSync`). See :func:`create_user` for the full
    contract.

    Args:
        client: An initialised synchronous ``Zep`` client.
        first_name: Optional first name, passed through to ``user.create``.
        last_name: Optional last name, passed through to ``user.create``.
        email: Optional email, passed through to ``user.create``.
        on_created: Optional synchronous hook run once, after the user is
            created.

    Returns:
        The created :class:`~zep_cloud.types.user.User`.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx), or any
            exception raised by ``on_created``.
    """
    user: User = client.user.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None:
        on_created(client, user)
        logger.info("on_created hook completed for user %s", user.uuid_)

    return user


def create_thread_sync(client: Zep, *, user_uuid: str) -> Thread:
    """Synchronous variant of :func:`create_thread`.

    Args:
        client: An initialised synchronous ``Zep`` client.
        user_uuid: The UUID of the Zep user that owns the thread. The user
            must already exist (see :func:`create_user_sync`).

    Returns:
        The created :class:`~zep_cloud.types.thread.Thread`.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, 5xx).
    """
    thread: Thread = client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
