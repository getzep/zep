"""Explicit, out-of-band Zep resource creation.

Zep v4 assigns the UUID of every user, thread, and graph. A create call does
not accept an address from the caller, so the v3 create-then-catch-conflict
pattern no longer applies. Each helper creates the resource and returns the
created object. The application stores ``uuid_`` in its own database and
gives the UUID to :class:`zep_autogen.ZepUserMemory` and to the tools.

A genuine failure (auth, network, 5xx) propagates to the caller. Creation runs
during onboarding, before the first turn, so a misconfiguration fails loudly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs once after Zep creates a user.
#:
#: The hook receives the Zep client and the UUID of the new user. Use the hook
#: to configure the ontology of the user graph, custom instructions, or user
#: summary instructions.
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]


async def create_user(
    client: AsyncZep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> User:
    """Create a Zep user and return it.

    Zep assigns ``user.uuid_`` and the UUID of the user graph
    (``user.graph_uuid``). Store both values in your own database. Give
    ``user.uuid_`` to :class:`zep_autogen.ZepUserMemory`.

    When ``on_created`` is provided, the hook is awaited before this function
    returns. An exception from the hook propagates to the caller, even though
    Zep created the user. Make the hook idempotent so that you can run the
    setup logic again against a user whose setup only partially completed.

    Args:
        client: An initialised ``AsyncZep`` client.
        first_name: Optional first name of the user.
        last_name: Optional last name of the user.
        email: Optional email address of the user.
        on_created: Optional async hook that runs once after Zep creates the
            user.

    Returns:
        The created ``User``, with ``uuid_`` and ``graph_uuid`` set by Zep.

    Raises:
        Exception: Any failure from the Zep SDK, or any exception from
            ``on_created``.
    """
    create_args: dict[str, Any] = {}
    if first_name is not None:
        create_args["first_name"] = first_name
    if last_name is not None:
        create_args["last_name"] = last_name
    if email is not None:
        create_args["email"] = email

    user: User = await client.user.create(**create_args)
    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None:
        if not user.uuid_:
            raise ValueError("Zep did not return a user UUID")
        await on_created(client, user.uuid_)
        logger.info("on_created hook completed for user %s", user.uuid_)

    return user


async def create_thread(client: AsyncZep, *, user_uuid: str) -> Thread:
    """Create a Zep thread for a user and return it.

    Zep assigns ``thread.uuid_``. Store the value in your own database and
    give it to :class:`zep_autogen.ZepUserMemory`.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the user that owns the thread. The user must
            already exist (see :func:`create_user`).

    Returns:
        The created ``Thread``, with ``uuid_`` set by Zep.

    Raises:
        Exception: Any failure from the Zep SDK.
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
