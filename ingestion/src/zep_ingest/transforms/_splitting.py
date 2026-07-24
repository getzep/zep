"""Text splitting primitives shared by TextChunker and LimitGuard.

The algorithm follows Zep's chunking cookbook
(https://help.getzep.com/chunking-large-documents): split at paragraph
boundaries first, fall back to sentence boundaries for oversize paragraphs,
and hard-slice only pathological unbroken strings.
"""

import json
import re
from collections.abc import Callable
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


def _split_json_value(
    value: Any, render: Callable[[Any], str], chunk_size: int
) -> list[str] | None:
    rendered = render(value)
    if len(rendered) <= chunk_size:
        return [rendered]

    if isinstance(value, str):
        if not value:
            return None
        pieces: list[str] = []
        start = 0
        while start < len(value):
            low, high, best = 1, len(value) - start, 0
            while low <= high:
                middle = (low + high) // 2
                candidate = render(value[start : start + middle])
                if len(candidate) <= chunk_size:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            if best == 0:
                return None
            pieces.append(render(value[start : start + best]))
            start += best
        return pieces

    if isinstance(value, list):
        if not value:
            return None
        list_pieces: list[str] = []
        list_group: list[Any] = []
        for item in value:
            candidate = render([*list_group, item])
            if len(candidate) <= chunk_size:
                list_group.append(item)
                continue
            if list_group:
                list_pieces.append(render(list_group))
                list_group = []
            nested = _split_json_value(item, lambda part: render([part]), chunk_size)
            if nested is None:
                return None
            list_pieces.extend(nested)
        if list_group:
            list_pieces.append(render(list_group))
        return list_pieces

    if isinstance(value, dict):
        if not value:
            return None
        dict_pieces: list[str] = []
        dict_group: dict[str, Any] = {}
        for key, item in value.items():
            candidate = render({**dict_group, key: item})
            if len(candidate) <= chunk_size:
                dict_group[key] = item
                continue
            if dict_group:
                dict_pieces.append(render(dict_group))
                dict_group = {}

            def render_item(part: Any, item_key: str = key) -> str:
                return render({item_key: part})

            nested = _split_json_value(item, render_item, chunk_size)
            if nested is None:
                return None
            dict_pieces.extend(nested)
        if dict_group:
            dict_pieces.append(render(dict_group))
        return dict_pieces

    return None


def split_json_top_level(text: str, chunk_size: int) -> list[str] | None:
    """Split JSON recursively while keeping every returned piece valid JSON."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return _split_json_value(parsed, json.dumps, chunk_size)
