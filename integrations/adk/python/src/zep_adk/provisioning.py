"""
Explicit, out-of-band Zep resource provisioning.

The ADK turn path (``ZepContextTool.process_llm_request``) never creates Zep
users or threads -- it only persists messages and retrieves context.  Callers
provision the Zep user and thread once, out-of-band, before the first turn
(e.g. during account/session onboarding), with :func:`create_user` and
:func:`create_thread`.

Zep v4 addresses every user and thread by a server-generated UUID.  Both
helpers return the created SDK object, so the caller reads ``uuid_`` (and,
for a user, ``graph_uuid``) from the response and stores the value in its own
database.  The integration never resolves a ``user_id`` or a ``thread_id``
back to a UUID at run time.

Both helpers fail loudly.  Any failure (auth, network, 5xx, a duplicate
``user_id``) propagates, so a misconfiguration is caught before the agent
ever runs.
"""

from __future__ import annotations

import logging

from zep_cloud.client import AsyncZep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)


async def create_user(
    client: AsyncZep,
    *,
    user_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
) -> User:
    """Create the Zep user and return the created ``User``.

    Read ``user.uuid_`` from the result and store it in your own database.
    The ADK turn path addresses the user by that UUID.  Read
    ``user.graph_uuid`` as well if you configure the user's graph (ontology,
    custom instructions) or if you use :class:`~zep_adk.graph_search_tool.ZepGraphSearchTool`
    or :class:`~zep_adk.memory_service.ZepMemoryService`.

    One-time per-user setup runs after this call returns.  Zep v4 gives every
    ``user.create`` call a new user, so there is no "already existed" case and
    no created signal.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: An optional human-readable name for the user.  The name is
            not an address.  Zep rejects a duplicate ``user_id`` with a
            conflict error.
        first_name: An optional first name.
        last_name: An optional last name.
        email: An optional email address.

    Returns:
        The created ``User``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, conflict, 5xx).
    """
    user: User = await client.user.create(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    logger.info("Created Zep user: %s", user.uuid_)
    return user


async def create_thread(
    client: AsyncZep,
    *,
    user_uuid: str,
    thread_id: str | None = None,
) -> Thread:
    """Create the Zep thread and return the created ``Thread``.

    Read ``thread.uuid_`` from the result and store it in your own database.
    The ADK turn path addresses the thread by that UUID.

    Args:
        client: An initialised ``AsyncZep`` client.
        user_uuid: The UUID of the Zep user that owns the thread.  The user
            must already exist (see :func:`create_user`).
        thread_id: An optional human-readable name for the thread.  The name
            is not an address.

    Returns:
        The created ``Thread``.

    Raises:
        Exception: Any failure from the Zep SDK (auth, network, conflict, 5xx).
    """
    thread: Thread = await client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread
