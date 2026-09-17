"""Document grouping helpers for Zep's ``document_id`` API field.

See https://help.getzep.com/documents. Text files, transcripts, emails, and
Slack sources are always documents. Slack is ingested as one episode per
message with a shared ``document_id`` for the Slack thread, plus a second
document of the channel's top-level messages (thread parents and unthreaded
posts) in order. JSON records stay standalone. Zep ``thread_id`` is reserved
for agent–user conversations on a user graph and is never set here.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from zep_ingest.exceptions import ConfigurationError

MAX_DOCUMENT_ID_LENGTH = 100
_ILLEGAL_DOCUMENT_ID_RE = re.compile(r"[/\?#]|[\x00-\x1f\x7f]")
_MESSAGE_ID = re.compile(r"<([^>]+)>")


def normalize_document_id(value: str) -> str:
    return value.strip()


def validate_document_id(value: str) -> None:
    """Validate a caller-supplied document_id before submission."""
    normalized = normalize_document_id(value)
    if not normalized:
        raise ConfigurationError("document_id must be a non-empty string")
    if len(normalized) > MAX_DOCUMENT_ID_LENGTH:
        raise ConfigurationError(
            f"document_id exceeds {MAX_DOCUMENT_ID_LENGTH} characters (got {len(normalized)})"
        )
    if _ILLEGAL_DOCUMENT_ID_RE.search(normalized):
        raise ConfigurationError(
            "document_id must not contain /, ?, #, or ASCII control characters"
        )


def document_id_from_parts(*parts: str, prefix: str) -> str:
    """Build a stable, API-safe document_id from logical parts."""
    key = ":".join(str(part) for part in parts if part is not None and str(part) != "")
    candidate = f"{prefix}:{key}" if key else prefix
    if len(candidate) <= MAX_DOCUMENT_ID_LENGTH and not _ILLEGAL_DOCUMENT_ID_RE.search(candidate):
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def document_id_for_path(path: Path, *, prefix: str = "file") -> str:
    """Stable id for one file (always a document, even as a single episode)."""
    return document_id_from_parts(str(path.resolve()), prefix=prefix)


def document_id_for_slack_thread(channel: str, thread_ts: str) -> str:
    """Shared document_id for every message episode in one Slack thread."""
    return document_id_from_parts(channel, thread_ts, prefix="slack")


def document_id_for_slack_channel(channel: str) -> str:
    """Shared document_id for a channel's top-level messages (in order)."""
    return document_id_from_parts(channel, prefix="slack-channel")


def _first_message_id(value: str) -> str | None:
    match = _MESSAGE_ID.search(value)
    if match:
        return match.group(1).strip()
    stripped = value.strip()
    return stripped or None


def document_id_for_email_thread(message: object, path: Path) -> str:
    """One document per email thread: References/In-Reply-To root, else Message-ID, else file."""
    for header in ("References", "In-Reply-To", "Message-ID"):
        raw = message.get(header) if hasattr(message, "get") else None  # type: ignore[union-attr]
        if raw:
            message_id = _first_message_id(str(raw))
            if message_id:
                return document_id_from_parts(message_id, prefix="email")
    return document_id_for_path(path, prefix="email")
