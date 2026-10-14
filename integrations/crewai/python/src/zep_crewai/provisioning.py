"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
A create call accepts no client-chosen identifier. The storage adapters in
this package therefore take ``user_uuid``, ``thread_uuid``, and
``graph_uuid``, and they never create a resource on the turn path.

:func:`ensure_user` and :func:`ensure_thread` run one time, during onboarding,
and they return the created resource. The application stores the returned
UUID in its own database and passes the UUID to the storage adapters and the
tools.

Both helpers call the Zep SDK create method directly, and a successful call
always creates a new resource. Neither helper calls ``lookup``. A failed
create (auth, network, conflict, 5xx) raises the error to the caller, so a
misconfiguration is caught before the agent runs.

Naming note: every sibling Zep framework integration exposes both an async
``ensure_user``/``ensure_thread`` pair and a synchronous ``_sync`` twin (for
frameworks that support both an ``AsyncZep`` and a ``Zep`` client). This
package is sync-only -- CrewAI's storage adapters are built exclusively on
the synchronous ``Zep`` client -- so there is no async variant to disambiguate
from, and the canonical names ``ensure_user``/``ensure_thread`` are used
directly (without a ``_sync`` suffix) for a sync implementation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import Thread, User

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the created ``User``.  The ``User`` carries the
#: ``uuid_`` of the user and the ``graph_uuid`` of the user graph, so the hook
#: can configure per-user ontology, custom instructions, or user summary
#: instructions.
UserSetupHook = Callable[[Zep, User], None]


def _conflict_uuid(exc: Exception) -> str | None:
    """Return ``details.uuid`` of a 409 error body, or ``None``.

    Defensive check: if a conflict response ever carries the UUID of the
    existing resource in ``details.uuid``, the helper resolves that resource
    by address (``user.get`` / ``thread.get``) without a ``lookup`` call.
    Zep v4 accepts no client-chosen identifier on create, so this path is
    not expected to run; a 409 without a usable UUID, a 400 that mentions
    "already exists" without details, and every other failure propagate.
    """
    status_code: Any = getattr(exc, "status_code", None)
    if status_code != 409:
        return None

    body = getattr(exc, "body", None)
    error = getattr(body, "error", body)
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        uuid = details.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
    return None


def ensure_user(
    client: Zep,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> tuple[User, bool]:
    """Create the Zep user, and return the user.

    Calls ``client.user.create(...)`` directly. Zep v4 accepts no
    client-chosen identifier on create, so a successful call always creates
    a new user; the returned ``User`` carries the server-generated ``uuid_``.
    A failed create raises the error to the caller -- this function never
    swallows a genuine failure and never calls ``lookup``. (If a conflict
    response ever carried the UUID of an existing user in ``details.uuid``,
    the helper would fetch that user with ``client.user.get(uuid)`` and
    return it.)

    Call this function one time, during onboarding, and store
    ``user.uuid_`` and ``user.graph_uuid`` in your own database. The storage
    adapters and the tools take those UUIDs. They never resolve a name.

    When the user is newly created and ``on_created`` is provided, the hook is
    called (with ``(client, user)``) **before** this function returns.  If
    the hook raises, the exception propagates to the caller even though the
    user was successfully created.  To recover from a hook failure, re-run
    the hook logic directly against the user (make it idempotent, i.e. safe
    to run against a user whose setup only partially completed).

    Args:
        client: An initialised ``Zep`` client.
        first_name: Optional first name, passed through to ``user.create``.
        last_name: Optional last name, passed through to ``user.create``.
        email: Optional email, passed through to ``user.create``.
        on_created: Optional hook run once for each user that this function
            newly creates.

    Returns:
        A tuple of the ``User`` and a flag. The flag is ``True`` when the
        user was newly created; it is ``False`` only if a conflict carried
        the UUID of an already-existing user that the helper then returned.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx),
            or any exception raised by ``on_created``.
    """
    create_kwargs: dict[str, Any] = {}
    if first_name is not None:
        create_kwargs["first_name"] = first_name
    if last_name is not None:
        create_kwargs["last_name"] = last_name
    if email is not None:
        create_kwargs["email"] = email

    try:
        user = client.user.create(**create_kwargs)
    except Exception as exc:
        conflict_uuid = _conflict_uuid(exc)
        if conflict_uuid is not None:
            logger.debug("Zep user %s already exists", conflict_uuid)
            return client.user.get(conflict_uuid), False
        raise

    logger.info("Created Zep user: %s", user.uuid_)

    if on_created is not None:
        on_created(client, user)
        logger.info("on_created hook completed for user %s", user.uuid_)

    return user, True


def ensure_thread(client: Zep, *, user_uuid: str) -> tuple[Thread, bool]:
    """Create the Zep thread, and return the thread.

    Calls ``client.thread.create(...)`` directly. Zep v4 accepts no
    client-chosen identifier on create, so a successful call always creates
    a new thread; the returned ``Thread`` carries the server-generated
    ``uuid_``. A failed create raises the error to the caller. (If a
    conflict response ever carried the UUID of an existing thread in
    ``details.uuid``, the helper would fetch that thread with
    ``client.thread.get(uuid)`` and return it.)

    Call this function one time, during onboarding, and store
    ``thread.uuid_`` in your own database.

    Args:
        client: An initialised ``Zep`` client.
        user_uuid: The UUID of the Zep user that owns the thread. The user
            must already exist (see :func:`ensure_user`).

    Returns:
        A tuple of the ``Thread`` and a flag. The flag is ``True`` when the
        thread was newly created; it is ``False`` only if a conflict
        carried the UUID of an already-existing thread that the helper
        then returned.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx).
    """
    try:
        thread = client.thread.create(user_uuid=user_uuid)
    except Exception as exc:
        conflict_uuid = _conflict_uuid(exc)
        if conflict_uuid is not None:
            logger.debug("Zep thread %s already exists", conflict_uuid)
            return client.thread.get(conflict_uuid), False
        raise

    logger.info("Created Zep thread: %s", thread.uuid_)
    return thread, True
