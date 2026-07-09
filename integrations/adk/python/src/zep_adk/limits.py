"""
Zep size-limit handling.

Zep rejects over-long payloads with a 400:
  - A thread message's ``content`` must be <= 4,096 characters.

Rather than letting an over-long message 400 and be silently dropped, we
truncate it (preferring a safety margin under the hard limit) and log a
warning. The warning carries ONLY lengths -- never message content -- so no
user text or PII reaches the logs.

Mirrors ``integrations/adk/typescript/src/limits.ts`` and the Go
implementation in ``integrations/adk/go/zep.go`` -- keep the constants and
semantics identical across languages.

The truncation constants below are numerically identical across all three
implementations, but the unit each language counts in differs: Python counts
Unicode code points, TypeScript counts UTF-16 code units, and Go counts bytes
(rune-safe -- truncation never splits a multi-byte UTF-8 sequence).
"""

from __future__ import annotations

import logging

#: Hard Zep limit on a thread message's ``content``, in characters.
MESSAGE_CONTENT_MAX = 4096

#: Target length we truncate over-long messages to. Kept under
#: :data:`MESSAGE_CONTENT_MAX` to leave headroom for any server-side encoding
#: differences.
MESSAGE_CONTENT_TRUNCATE_TO = 4000

logger = logging.getLogger(__name__)


def truncate_message_content(content: str, label: str = "message") -> str:
    """Truncate a thread-message content string to Zep's message limit.

    Logs a warning (lengths only -- never content) when truncation happens.
    Content within the limit is returned unchanged. Over-long content is
    truncated to :data:`MESSAGE_CONTENT_TRUNCATE_TO` characters so the
    message is persisted rather than dropped on a 400.

    Args:
        content: The message content to bound.
        label: A short, non-PII label for the message (e.g. ``"user"``,
            ``"assistant"``) included in the warning to aid debugging.

    Returns:
        The original content, or a truncated copy when it exceeds the limit.
    """
    if len(content) <= MESSAGE_CONTENT_MAX:
        return content

    truncated = content[:MESSAGE_CONTENT_TRUNCATE_TO]
    logger.warning(
        "Truncated %s content before persisting to Zep: original %d chars "
        "exceeds the %d-char message limit; truncated to %d chars.",
        label,
        len(content),
        MESSAGE_CONTENT_MAX,
        len(truncated),
    )
    return truncated
