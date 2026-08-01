"""Internal text helpers for safely persisting content to Zep.

Zep enforces hard size limits and returns a ``400 Bad Request`` when they are
exceeded:

* A single thread message's ``content`` must be at most **4,096** characters
  (see https://help.getzep.com/adding-messages#message-limits).
* A single ``graph.add`` payload must be at most **10,000** characters.

This module centralises the size guards so they are not duplicated across the
store's write paths.  Rather than letting an oversize payload trigger a 400 that
gets swallowed (silently losing the write), we truncate to a safe ceiling and
emit a warning.

The warning carries **only** lengths -- never message content or any PII --
to comply with the repository's logging rules.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Hard per-message content limit enforced by Zep's thread API.
ZEP_MESSAGE_CONTENT_LIMIT = 4096

#: Truncation ceiling for thread messages.  Kept below the hard 4,096 limit so
#: there is headroom and the persisted content is comfortably within bounds.
MESSAGE_TRUNCATE_LIMIT = 4000

#: Hard per-call content limit enforced by Zep's ``graph.add`` API.
ZEP_GRAPH_DATA_LIMIT = 10_000

#: Truncation ceiling for ``graph.add`` payloads.  Kept below the hard 10,000
#: limit so there is headroom.
GRAPH_DATA_TRUNCATE_LIMIT = 9900


def truncate_message_content(
    content: str,
    *,
    label: str,
    limit: int = MESSAGE_TRUNCATE_LIMIT,
) -> str:
    """Truncate ``content`` to ``limit`` characters, warning if truncation occurs.

    Zep rejects messages whose content exceeds 4,096 characters with a 400 error.
    Truncating here keeps the turn within bounds so it is persisted rather than
    silently lost.

    Args:
        content: The raw message text.
        label: A short, non-sensitive identifier for the message kind (e.g.
            ``"user message"`` or ``"assistant message"``) used only in the log
            line.  Must not contain content or PII.
        limit: The maximum number of characters to keep.  Defaults to
            :data:`MESSAGE_TRUNCATE_LIMIT`.

    Returns:
        The original content when within ``limit``, otherwise the first
        ``limit`` characters.
    """
    original_length = len(content)
    if original_length <= limit:
        return content

    # Log lengths only -- never the content itself (PII / repo rule).
    logger.warning(
        "Truncating %s before persisting to Zep: %d chars exceeds limit of %d; "
        "keeping first %d chars.",
        label,
        original_length,
        limit,
        limit,
    )
    return content[:limit]


def truncate_graph_data(
    content: str,
    *,
    label: str = "graph data",
    limit: int = GRAPH_DATA_TRUNCATE_LIMIT,
) -> str:
    """Truncate ``content`` for ``graph.add``, warning if truncation occurs.

    Args:
        content: The raw graph payload text.
        label: A short, non-sensitive identifier used only in the log line.
            Must not contain content or PII.
        limit: The maximum number of characters to keep.  Defaults to
            :data:`GRAPH_DATA_TRUNCATE_LIMIT`.

    Returns:
        The original content when within ``limit``, otherwise the first
        ``limit`` characters.
    """
    original_length = len(content)
    if original_length <= limit:
        return content

    logger.warning(
        "Truncating %s before graph.add to Zep: %d chars exceeds limit of %d; "
        "keeping first %d chars.",
        label,
        original_length,
        limit,
        limit,
    )
    return content[:limit]
