"""
Zep size-limit handling.

Zep rejects over-long payloads with a 400:
  - A thread message's ``content`` must be <= 4,096 characters.
  - A single ``graph.add`` call's ``data`` must be <= 10,000 characters.

Rather than letting an over-long payload 400 and be silently dropped, we
truncate it (preferring a safety margin under the hard limit) and log a
warning. The warning carries ONLY lengths -- never content -- so no user text
or PII reaches the logs.

Mirrors ``integrations/autogen/python/src/zep_autogen/limits.py`` and
``integrations/ag2/python/src/zep_ag2/tools.py`` -- keep the message-content
constants identical across Python integrations. ``GRAPH_MAX_CHARS = 9900`` is
a safety margin under Zep's documented 10,000-char ``graph.add`` ceiling,
matching the ag2/autogen precedent.
"""

from __future__ import annotations

import logging

#: Hard Zep limit on a thread message's ``content``, in characters.
MESSAGE_CONTENT_MAX = 4096

#: Target length we truncate over-long messages to. Kept under
#: :data:`MESSAGE_CONTENT_MAX` to leave headroom for any server-side encoding
#: differences.
MESSAGE_CONTENT_TRUNCATE_TO = 4000

#: Safety margin under Zep's documented 10,000-char ``graph.add`` ceiling.
GRAPH_MAX_CHARS = 9900

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


def truncate_graph_data(data: str, label: str = "graph data") -> str:
    """Truncate a ``graph.add`` payload to Zep's per-call data limit.

    Logs a warning (lengths only -- never content) when truncation happens.
    Data within the limit is returned unchanged. Over-long data is truncated
    to :data:`GRAPH_MAX_CHARS` characters so the call is persisted rather
    than dropped on a 400.

    Args:
        data: The graph data payload to bound.
        label: A short, non-PII label included in the warning to aid
            debugging.

    Returns:
        The original data, or a truncated copy when it exceeds the limit.
    """
    if len(data) <= GRAPH_MAX_CHARS:
        return data

    truncated = data[:GRAPH_MAX_CHARS]
    logger.warning(
        "Truncated %s before sending to Zep: original %d chars exceeds the "
        "%d-char graph.add limit; truncated to %d chars.",
        label,
        len(data),
        GRAPH_MAX_CHARS,
        len(truncated),
    )
    return truncated
