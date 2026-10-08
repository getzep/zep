"""Short opaque handles for graph items.

Tools return handles such as n1, e1, and p3 instead of raw UUIDs. The model
passes handles back to tools that need them. The registry deduplicates by UUID
across the whole run, so the same node always has the same handle, and marks
items the model has already received as "seen".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Entry:
    handle: str
    uuid: str
    seen: bool = False


@dataclass
class HandleRegistry:
    _by_uuid: dict[str, _Entry] = field(default_factory=dict)
    _by_handle: dict[str, _Entry] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=lambda: {"n": 0, "e": 0, "p": 0})

    def register(self, uuid: str, prefix: str) -> str:
        """Return the handle for a UUID, creating it on first use."""
        if uuid in self._by_uuid:
            return self._by_uuid[uuid].handle
        self._counters[prefix] += 1
        handle = f"{prefix}{self._counters[prefix]}"
        entry = _Entry(handle=handle, uuid=uuid)
        self._by_uuid[uuid] = entry
        self._by_handle[handle] = entry
        return handle

    def mark_seen(self, uuid_or_handle: str) -> None:
        entry = self._by_uuid.get(uuid_or_handle) or self._by_handle.get(uuid_or_handle)
        if entry is not None:
            entry.seen = True

    def is_seen(self, handle: str) -> bool:
        entry = self._by_handle.get(handle)
        return entry.seen if entry else False

    def resolve(self, handle: str, prefix: str | None = None) -> str:
        """Return the UUID for a handle. Raises KeyError for unknown handles."""
        entry = self._by_handle.get(handle)
        if entry is None or (prefix and not handle.startswith(prefix)):
            raise KeyError(f"unknown handle: {handle}")
        return entry.uuid

    def uuid_to_handle(self, uuid: str) -> str | None:
        entry = self._by_uuid.get(uuid)
        return entry.handle if entry else None
