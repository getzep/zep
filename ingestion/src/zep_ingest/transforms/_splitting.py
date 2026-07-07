"""Text splitting primitives shared by TextChunker and LimitGuard.

The algorithm follows Zep's chunking cookbook
(https://help.getzep.com/chunking-large-documents): split at paragraph
boundaries first, fall back to sentence boundaries for oversize paragraphs,
and hard-slice only pathological unbroken strings.
"""

import json
import re
from typing import Any

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def hard_split(text: str, chunk_size: int) -> list[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _overlap_tail(text: str, overlap: int) -> str:
    """Last ``overlap`` chars of ``text``, rounded forward to a whitespace boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    first_space = tail.find(" ")
    if first_space > 0:
        tail = tail[first_space + 1 :]
    return tail.strip()


def _split_long_paragraph(paragraph: str, chunk_size: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_BOUNDARY.split(paragraph):
        if len(sentence) > chunk_size:
            if current.strip():
                pieces.append(current.strip())
                current = ""
            pieces.extend(hard_split(sentence, chunk_size))
            continue
        if current and len(current) + 1 + len(sentence) > chunk_size:
            pieces.append(current.strip())
            tail = _overlap_tail(current, overlap)
            current = tail if tail and len(tail) + 1 + len(sentence) <= chunk_size else ""
        current = f"{current} {sentence}" if current else sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def split_text(text: str, chunk_size: int, overlap: int = 0) -> list[str]:
    """Split ``text`` into chunks of at most ``chunk_size`` characters."""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) > chunk_size:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, chunk_size, overlap))
            continue
        if current and len(current) + 2 + len(paragraph) > chunk_size:
            chunks.append(current.strip())
            tail = _overlap_tail(current, overlap)
            current = tail if tail and len(tail) + 2 + len(paragraph) <= chunk_size else ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        chunks.append(current.strip())
    return chunks


def split_lines(text: str, chunk_size: int) -> list[str]:
    """Pack whole lines into chunks (for message-formatted episodes)."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(line) > chunk_size:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(hard_split(line, chunk_size))
            continue
        if current and len(current) + 1 + len(line) > chunk_size:
            pieces.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        pieces.append(current)
    return pieces


def split_json_top_level(text: str, chunk_size: int) -> list[str] | None:
    """Split a JSON document at the top level into valid JSON pieces.

    Returns None when the text is not parseable JSON or cannot be split
    (e.g. a scalar) — callers fall back to a hard split.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    def pack(items: list[Any], render: Any) -> list[str]:
        pieces: list[str] = []
        group: list[Any] = []
        for item in items:
            candidate = render(group + [item])
            if group and len(candidate) > chunk_size:
                pieces.append(render(group))
                group = []
            group.append(item)
        if group:
            pieces.append(render(group))
        return pieces

    if isinstance(parsed, list):
        return pack(parsed, json.dumps)
    if isinstance(parsed, dict):
        return pack(list(parsed.items()), lambda items: json.dumps(dict(items)))
    return None
