"""JsonNormalizer: the docs' JSON-shaping algorithm, automated.

Zep extracts best from JSON that is small, flat, self-contained, and
representing one entity (help.getzep.com/adding-json-best-practices). Applied
top-down per the docs: flatten deep nesting → explode long lists (with a
contextualizing field) → extract long strings as text → split wide objects
into pieces that duplicate the identity fields.
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from zep_ingest.types import Episode


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def _flatten(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{path}_"))
        else:
            flat[path] = value
    return flat


class JsonNormalizer:
    def __init__(
        self,
        *,
        max_props: int = 6,
        max_depth: int = 3,
        max_list_items: int = 1,
        long_string_chars: int = 1_000,
        identity_fields: Sequence[str] = ("id", "name", "description"),
    ) -> None:
        self.max_props = max_props
        self.max_depth = max_depth
        self.max_list_items = max_list_items
        self.long_string_chars = long_string_chars
        self.identity_fields = tuple(identity_fields)
        self.warnings: list[str] = []

    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for episode in episodes:
            if episode.data_type != "json":
                yield episode
                continue
            try:
                parsed = json.loads(episode.data)
            except (json.JSONDecodeError, ValueError):
                self.warnings.append(
                    "A json episode is not parseable JSON; passed through unchanged."
                )
                yield episode
                continue
            records = parsed if isinstance(parsed, list) else [parsed]
            for record in records:
                if not isinstance(record, dict):
                    yield self._clone(episode, json.dumps(record), "json")
                    continue
                yield from self._normalize(record, episode)

    def _normalize(self, record: dict[str, Any], episode: Episode) -> Iterator[Episode]:
        changed = False
        if _depth(record) > self.max_depth:
            record = _flatten(record)
            changed = True
        identity = {k: record[k] for k in self.identity_fields if k in record}
        rest = {k: v for k, v in record.items() if k not in identity}

        list_pieces: list[dict[str, Any]] = []
        for key in list(rest):
            value = rest[key]
            if isinstance(value, list) and len(value) > self.max_list_items:
                item_type = key[:-1] if key.endswith("s") and len(key) > 1 else key
                for element in value:
                    piece = dict(identity)
                    piece["item_type"] = item_type
                    if isinstance(element, dict):
                        piece.update(element)
                    else:
                        piece["value"] = element
                    list_pieces.append(piece)
                del rest[key]
                changed = True

        extracted_texts: list[str] = []
        for key in list(rest):
            value = rest[key]
            if isinstance(value, str) and len(value) > self.long_string_chars:
                label = identity.get("name") or identity.get("id") or "this record"
                extracted_texts.append(f"Text from field '{key}' of {label}:\n\n{value}")
                del rest[key]
                changed = True

        if len(rest) > self.max_props:
            keys = list(rest)
            groups = [keys[i : i + self.max_props] for i in range(0, len(keys), self.max_props)]
            pieces = [{**identity, **{k: rest[k] for k in group}} for group in groups]
            changed = True
        else:
            pieces = [{**identity, **rest}]

        if not changed:
            yield self._clone(episode, json.dumps(record), "json")
            return
        for piece in pieces:
            # an identity-less record can be fully consumed by list explosion /
            # string extraction, leaving an empty piece worth nothing
            if piece:
                yield self._clone(episode, json.dumps(piece), "json")
        for piece in list_pieces:
            yield self._clone(episode, json.dumps(piece), "json")
        for text in extracted_texts:
            yield self._clone(episode, text, "text")

    @staticmethod
    def _clone(episode: Episode, data: str, data_type: str) -> Episode:
        return Episode(
            data=data,
            data_type=data_type,  # type: ignore[arg-type]
            created_at=episode.created_at,
            metadata=dict(episode.metadata) if episode.metadata else None,
            source_description=episode.source_description,
        )
