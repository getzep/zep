"""
Explicit, out-of-band Zep resource provisioning.

The LangGraph node-helper path (:func:`~zep_langgraph.context.get_zep_context` /
:func:`~zep_langgraph.persistence.persist_messages`) never creates Zep users or
threads -- it only persists messages and retrieves context. Callers are
expected to provision the Zep user and thread once, out-of-band, before the
first turn (e.g. during account/session onboarding), using :func:`ensure_user`
and :func:`ensure_thread` (or their synchronous twins, :func:`ensure_user_sync`
and :func:`ensure_thread_sync`, for use with the synchronous ``Zep`` client).

Both helpers are **create-then-catch-conflict**: they call the Zep SDK's
create method directly and treat an "already exists" error as success, rather
than checking for existence first (which is racy and costs an extra
round-trip). Genuine failures (auth, network, 5xx) always raise -- out-of-band
provisioning is meant to fail loudly so misconfiguration is caught before the
agent ever runs, not swallowed into a silent no-op.

These are plain module-level functions with no instance caching -- callers
that want to avoid redundant calls (e.g. per-request handlers) should cache
the "already provisioned" result themselves (for example, alongside their
session store).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from zep_cloud.client import AsyncZep, Zep

logger = logging.getLogger(__name__)

#: Type alias for a user-setup hook that runs once after a Zep user is created.
#:
#: Receives the Zep client and the newly created user ID.  Use this to configure
#: per-user ontology, custom instructions, or user summary instructions.
UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]

#: Synchronous twin of :data:`UserSetupHook`, for use with :func:`ensure_user_sync`.
UserSetupHookSync = Callable[[Zep, str], None]


def _is_already_exists_error(exc: Exception) -> bool:
    """Detect whether ``exc`` represents a "resource already exists" conflict.

    Handles both typed and message-based shapes returned by the Zep SDK:

    * A 409 status code (``zep_cloud.errors.ConflictError``, or any
      ``ApiError``-like object exposing ``status_code == 409``).
    * A 400 ``BadRequestError`` (or similar) whose message mentions
      "already exists".
    * An **untyped** exception (no ``status_code``) whose string
      representation mentions "already exists" or "conflict" (fallback for
      untyped/legacy error shapes).

    A plain 404 (not found) or any other genuine failure is **not** treated
    as an already-exists conflict.  In particular, a typed error with any
    other status code (e.g. a 500 whose message happens to mention
    "conflict") is a genuine failure and must propagate.
    """
    status_code: Any = getattr(exc, "status_code", None)
    if status_code == 409:
        return True

    text = str(exc).lower()
    if status_code == 400 and "already exists" in text:
        return True

    # Fallback heuristic for untyped/legacy error shapes only: an error that
    # carries a known non-conflict status code is a genuine failure, no
    # matter what its message says.
    if status_code is not None:
        return False
    return "already exists" in text or "conflict" in text


async def ensure_user(
    client: AsyncZep,
    *,
    user_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHook | None = None,
) -> bool:
    """Idempotently ensure the Zep user exists.

    Calls ``client.user.add(...)`` directly (create-then-catch-conflict).  If
    the call fails with an "already exists" conflict, the user is assumed to
    already be provisioned and the call returns ``False`` without raising.
    Any other failure (auth, network, 5xx) propagates to the caller -- this
    function never swallows genuine errors.

    When the user is newly created and ``on_created`` is provided, the hook is
    awaited (with ``(client, user_id)``) **before** this function returns.  If
    the hook raises, the exception propagates to the caller even though the
    user was successfully created.  A later ``ensure_user`` call will **not**
    re-run the hook -- the user now exists, so it takes the already-exists
    path.  To recover from a hook failure, re-run the hook logic directly
    against the user (make it idempotent, i.e. safe to run against a user
    whose setup only partially completed).

    Args:
        client: An initialised ``AsyncZep`` client.
        user_id: The Zep user ID to create.
        first_name: Optional first name, passed through to ``user.add``.
        last_name: Optional last name, passed through to ``user.add``.
        email: Optional email, passed through to ``user.add``.
        on_created: Optional async hook run exactly once, only when the user
            is newly created.

    Returns:
        ``True`` if the user was newly created, ``False`` if it already
        existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx),
            or any exception raised by ``on_created``.
    """
    try:
        await client.user.add(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )
    except Exception as exc:
        if _is_already_exists_error(exc):
            logger.debug("Zep user %s already exists", user_id)
            return False
        raise

    logger.info("Created Zep user: %s", user_id)

    if on_created is not None:
        await on_created(client, user_id)
        logger.info("on_created hook completed for user %s", user_id)

    return True


async def ensure_thread(client: AsyncZep, *, thread_id: str, user_id: str) -> bool:
    """Idempotently ensure the Zep thread exists.

    Calls ``client.thread.create(...)`` directly (create-then-catch-conflict).
    If the call fails with an "already exists" conflict, the thread is
    assumed to already be provisioned and the call returns ``False`` without
    raising.  Any other failure (auth, network, 5xx) propagates to the
    caller.

    Args:
        client: An initialised ``AsyncZep`` client.
        thread_id: The Zep thread ID to create.
        user_id: The Zep user ID that owns the thread.  The user must already
            exist (see :func:`ensure_user`).

    Returns:
        ``True`` if the thread was newly created, ``False`` if it already
        existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx).
    """
    try:
        await client.thread.create(thread_id=thread_id, user_id=user_id)
    except Exception as exc:
        if _is_already_exists_error(exc):
            logger.debug("Zep thread %s already exists", thread_id)
            return False
        raise

    logger.info("Created Zep thread: %s", thread_id)
    return True


def ensure_user_sync(
    client: Zep,
    *,
    user_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    on_created: UserSetupHookSync | None = None,
) -> bool:
    """Synchronous variant of :func:`ensure_user`.

    Uses a synchronous ``Zep`` client and a synchronous ``on_created`` hook
    (:data:`UserSetupHookSync`). See :func:`ensure_user` for the full
    create-then-catch-conflict / error-propagation contract.

    Args:
        client: An initialised synchronous ``Zep`` client.
        user_id: The Zep user ID to create.
        first_name: Optional first name, passed through to ``user.add``.
        last_name: Optional last name, passed through to ``user.add``.
        email: Optional email, passed through to ``user.add``.
        on_created: Optional synchronous hook run exactly once, only when the
            user is newly created.

    Returns:
        ``True`` if the user was newly created, ``False`` if it already
        existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx),
            or any exception raised by ``on_created``.
    """
    try:
        client.user.add(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
        )
    except Exception as exc:
        if _is_already_exists_error(exc):
            logger.debug("Zep user %s already exists", user_id)
            return False
        raise

    logger.info("Created Zep user: %s", user_id)

    if on_created is not None:
        on_created(client, user_id)
        logger.info("on_created hook completed for user %s", user_id)

    return True


def ensure_thread_sync(client: Zep, *, thread_id: str, user_id: str) -> bool:
    """Synchronous variant of :func:`ensure_thread`.

    Args:
        client: An initialised synchronous ``Zep`` client.
        thread_id: The Zep thread ID to create.
        user_id: The Zep user ID that owns the thread.  The user must already
            exist (see :func:`ensure_user_sync`).

    Returns:
        ``True`` if the thread was newly created, ``False`` if it already
        existed.

    Raises:
        Exception: Any genuine failure from the Zep SDK (auth, network, 5xx).
    """
    try:
        client.thread.create(thread_id=thread_id, user_id=user_id)
    except Exception as exc:
        if _is_already_exists_error(exc):
            logger.debug("Zep thread %s already exists", thread_id)
            return False
        raise

    logger.info("Created Zep thread: %s", thread_id)
    return True
