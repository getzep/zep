"""
Explicit, out-of-band Zep resource provisioning.

Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
A ``user_id`` or a ``thread_id`` is a name, not an address. The storage
adapters in this package therefore take ``user_uuid``, ``thread_uuid``, and
``graph_uuid``, and they never create a resource on the turn path.

:func:`ensure_user` and :func:`ensure_thread` run one time, during onboarding,
and they return the created or existing resource. The application stores the
returned UUID in its own database and passes the UUID to the storage adapters
and the tools.

Both helpers are **create-then-catch-conflict**: they call the Zep SDK create
method directly, and when Zep reports a conflict they read the UUID of the
existing resource from the error response and fetch that resource by UUID.
Zep v4 returns ``409 resource_already_exists`` for a name that is already in
use, and the error body carries ``details.uuid``. Neither helper calls
``lookup``. Genuine failures (auth, network, 5xx) always raise, so a
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
    """Return the UUID of the conflicting resource, or ``None``.

    Zep v4 answers a create conflict with ``409 resource_already_exists`` and
    puts the UUID of the existing resource in ``details.uuid`` of the error
    body. Only that shape counts as an already-exists conflict here: the UUID
    lets the caller resolve the resource by address (``user.get`` /
    ``thread.get``) without a ``lookup`` call. A 409 whose body does not
    carry a usable UUID, a 400 that mentions "already exists" without
    details, and every other failure are genuine errors and propagate.
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
    user_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> tuple[User, bool]:
    """Idempotently ensure the Zep user exists, and return the user.

    Calls ``client.user.create(...)`` directly (create-then-catch-conflict).
    If the call fails with an "already exists" conflict, the helper reads the
    UUID of the existing user from the error response and fetches it with
    ``client.user.get(uuid)``. Any other failure (auth, network, 5xx)
    propagates to the caller -- this function never swallows genuine errors
    and never calls ``lookup``.

    Call this function one time, during onboarding, and store
    ``user.uuid_`` and ``user.graph_uuid`` in your own database. The storage
    adapters and the tools take those UUIDs. They never resolve a name.

    When the user is newly created and ``on_created`` is provided, the hook is
    called (with ``(client, user)``) **before** this function returns.  If
    the hook raises, the exception propagates to the caller even though the
    user was successfully created.  A later ``ensure_user`` call will **not**
    re-run the hook -- the user now exists, so it takes the already-exists
    path.  To recover from a hook failure, re-run the hook logic directly
    against the user (make it idempotent, i.e. safe to run against a user
    whose setup only partially completed).

    Args:
        client: An initialised ``Zep`` client.
        user_id: The Zep user name to create.
        first_name: Optional first name, passed through to ``user.create``.
        last_name: Optional last name, passed through to ``user.create``.
        email: Optional email, passed through to ``user.create``.
        on_created: Optional hook run exactly once, only when the user is
            newly created.

    Returns:
        A tuple of the ``User`` and a flag. The flag is ``True`` when the
        user was newly created and ``False`` when the user already existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx),
            or any exception raised by ``on_created``.
    """
    create_kwargs: dict[str, Any] = {"user_id": user_id}
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
            logger.debug("Zep user %s already exists", user_id)
            return client.user.get(conflict_uuid), False
        raise

    logger.info("Created Zep user: %s", user_id)

    if on_created is not None:
        on_created(client, user)
        logger.info("on_created hook completed for user %s", user_id)

    return user, True


def ensure_thread(client: Zep, *, thread_id: str, user_uuid: str) -> tuple[Thread, bool]:
    """Idempotently ensure the Zep thread exists, and return the thread.

    Calls ``client.thread.create(...)`` directly (create-then-catch-conflict).
    If the call fails with an "already exists" conflict, the helper reads the
    UUID of the existing thread from the error response and fetches it with
    ``client.thread.get(uuid)``. Any other failure (auth, network, 5xx)
    propagates to the caller.

    Call this function one time, during onboarding, and store
    ``thread.uuid_`` in your own database.

    Args:
        client: An initialised ``Zep`` client.
        thread_id: The Zep thread name to create.
        user_uuid: The UUID of the Zep user that owns the thread. The user
            must already exist (see :func:`ensure_user`).

    Returns:
        A tuple of the ``Thread`` and a flag. The flag is ``True`` when the
        thread was newly created and ``False`` when the thread already
        existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx).
    """
    try:
        thread = client.thread.create(user_uuid=user_uuid, thread_id=thread_id)
    except Exception as exc:
        conflict_uuid = _conflict_uuid(exc)
        if conflict_uuid is not None:
            logger.debug("Zep thread %s already exists", thread_id)
            return client.thread.get(conflict_uuid), False
        raise

    logger.info("Created Zep thread: %s", thread_id)
    return thread, True
