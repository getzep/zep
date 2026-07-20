"""SlackExportLoader: standard Slack workspace exports → message episodes.

Accepts an export .zip (read in place) or an extracted directory. Resolves
user IDs to display names via the export's users.json — or org_users.json in
an Enterprise Grid organization export — and warns when that roster is missing
or does not cover every referenced user (either case leaves raw Slack IDs in
the graph, which degrades entity extraction). Normalizes Slack markup, skips
join/leave/bot noise, groups messages by thread (the semantic unit Zep
extracts best from), and stamps every episode with the original message
timestamp so backfilled facts carry the correct valid_at timeline.
"""

import html
import json
import re
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode

DEFAULT_SKIP_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_archive",
        "channel_unarchive",
        "channel_name",
        "channel_purpose",
        "channel_topic",
        "bot_add",
        "bot_remove",
        "pinned_item",
        "reminder_add",
    }
)

_MENTION = re.compile(r"<@(\w+)>")
_CHANNEL_REF = re.compile(r"<#\w+\|([^>]+)>")
_CHANNEL_REF_BARE = re.compile(r"<#(\w+)>")
_SPECIAL = re.compile(r"<!(\w+)(?:\|[^>]*)?>")
_LINK_LABELED = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_LINK_BARE = re.compile(r"<(https?://[^>]+)>")


@dataclass(slots=True)
class SlackMessage:
    sender: str
    text: str
    ts: str
    channel: str
    thread_ts: str | None = None


class _DirReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_json(self, relpath: str) -> Any | None:
        file = self.path / relpath
        if not file.exists():
            return None
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Unparseable JSON in Slack export: {relpath}") from error

    def channel_dirs(self) -> list[str]:
        return sorted(p.name for p in self.path.iterdir() if p.is_dir())

    def day_files(self, channel: str) -> list[str]:
        directory = self.path / channel
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.glob("*.json"))


class _ZipReader:
    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path)
        names = self.zip.namelist()
        # tolerate a single top-level folder wrapping the export
        self.prefix = ""
        if names and "users.json" not in names and "channels.json" not in names:
            roots = {name.split("/", 1)[0] for name in names if "/" in name}
            if len(roots) == 1:
                candidate = next(iter(roots)) + "/"
                if any(name == candidate + "users.json" for name in names) or any(
                    name == candidate + "channels.json" for name in names
                ):
                    self.prefix = candidate
        self.names = [n[len(self.prefix) :] for n in names if n.startswith(self.prefix)]

    def read_json(self, relpath: str) -> Any | None:
        if relpath not in self.names:
            return None
        try:
            return json.loads(self.zip.read(self.prefix + relpath).decode("utf-8"))
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Unparseable JSON in Slack export: {relpath}") from error

    def channel_dirs(self) -> list[str]:
        return sorted({name.split("/", 1)[0] for name in self.names if "/" in name})

    def day_files(self, channel: str) -> list[str]:
        prefix = channel + "/"
        return sorted(
            name[len(prefix) :]
            for name in self.names
            if name.startswith(prefix) and name.endswith(".json")
        )


def _default_formatter(message: SlackMessage) -> str:
    stamp = datetime.fromtimestamp(float(message.ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
    return f"{message.sender} (Slack #{message.channel}, {stamp} UTC): {message.text}"


class SlackExportLoader:
    def __init__(
        self,
        path: str | Path,
        *,
        channels: Sequence[str] | None = None,
        grouping: Literal["thread", "message"] = "thread",
        include_bots: bool = False,
        skip_subtypes: frozenset[str] = DEFAULT_SKIP_SUBTYPES,
        formatter: Callable[[SlackMessage], str] | None = None,
    ) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise ConfigurationError(f"Slack export not found: {self.path}")
        self.channels = list(channels) if channels is not None else None
        self.grouping = grouping
        self.include_bots = include_bots
        self.skip_subtypes = skip_subtypes
        self.formatter = formatter or _default_formatter
        self.warnings: list[str] = []
        self._unresolved_users: set[str] = set()

    def load(self) -> Iterator[Episode]:
        reader = _ZipReader(self.path) if self.path.is_file() else _DirReader(self.path)
        self._unresolved_users = set()
        roster = self._read_roster(reader)
        users = self._user_map(roster)
        channels = self._channel_names(reader)
        if roster is None and not channels:
            raise ConfigurationError(
                f"{self.path} does not look like a Slack export: it has no user "
                "roster (users.json / org_users.json) and no channels. Point at an "
                "unzipped export directory or the export .zip itself."
            )
        if roster is None:
            self.warnings.append(
                "No users.json or org_users.json roster found in the Slack export; "
                "every message author and @mention will be ingested as a raw Slack "
                "ID (e.g. U012AB3CD) instead of a display name, which degrades entity "
                "extraction. Verify this is a complete workspace export."
            )
        for channel in channels:
            yield from self._load_channel(reader, channel, users)
        if self._unresolved_users:
            self.warnings.append(
                f"{len(self._unresolved_users)} Slack user ID(s) referenced in "
                "messages were absent from the roster (typically deactivated, bot, "
                "or Slack Connect users) and were left as raw IDs."
            )

    @staticmethod
    def _read_roster(reader: _DirReader | _ZipReader) -> Any:
        """The user roster: users.json in a standard export, or org_users.json in an
        Enterprise Grid organization export. None when neither file is present."""
        roster = reader.read_json("users.json")
        if roster is None:
            roster = reader.read_json("org_users.json")
        return roster

    @staticmethod
    def _user_map(roster: Any) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for user in roster or []:
            profile = user.get("profile") or {}
            mapping[user["id"]] = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("name")
                or user["id"]
            )
        return mapping

    def _channel_names(self, reader: _DirReader | _ZipReader) -> list[str]:
        channels_json = reader.read_json("channels.json")
        if channels_json:
            available = [c["name"] for c in channels_json]
        else:
            available = reader.channel_dirs()
        if self.channels is None:
            return available
        missing = [c for c in self.channels if c not in available]
        if missing:
            raise ConfigurationError(
                f"Channel(s) not present in the Slack export: {', '.join(missing)}. "
                f"Available: {', '.join(available)}."
            )
        return [c for c in available if c in self.channels]

    def _load_channel(
        self, reader: _DirReader | _ZipReader, channel: str, users: dict[str, str]
    ) -> Iterator[Episode]:
        messages: list[SlackMessage] = []
        seen_ts: set[str] = set()
        for day_file in reader.day_files(channel):
            raw_messages = reader.read_json(f"{channel}/{day_file}") or []
            for raw in raw_messages:
                message = self._parse(raw, channel, users)
                if message is None or message.ts in seen_ts:
                    continue
                seen_ts.add(message.ts)
                messages.append(message)
        messages.sort(key=lambda m: float(m.ts))
        if self.grouping == "message":
            for message in messages:
                yield self._episode([message], channel)
            return
        threads: dict[str, list[SlackMessage]] = {}
        order: list[str] = []
        for message in messages:
            key = message.thread_ts or message.ts
            if key not in threads:
                threads[key] = []
                order.append(key)
            threads[key].append(message)
        for key in sorted(order, key=float):
            yield self._episode(threads[key], channel)

    def _parse(
        self, raw: dict[str, Any], channel: str, users: dict[str, str]
    ) -> SlackMessage | None:
        if raw.get("subtype") in self.skip_subtypes:
            return None
        if raw.get("bot_id") and not self.include_bots:
            return None
        text = self._normalize_text(raw.get("text") or "", users).strip()
        if not text:
            return None
        ts = raw.get("ts")
        if ts is None:
            return None
        if raw.get("user"):
            sender = self._resolve(raw["user"], users)
        else:
            sender = raw.get("username") or "bot"
        return SlackMessage(
            sender=sender,
            text=text,
            ts=ts,
            channel=channel,
            thread_ts=raw.get("thread_ts"),
        )

    def _resolve(self, user_id: str, users: dict[str, str]) -> str:
        """Map a Slack user ID to a display name, recording IDs the roster misses."""
        name = users.get(user_id)
        if name is None:
            self._unresolved_users.add(user_id)
            return user_id
        return name

    def _normalize_text(self, text: str, users: dict[str, str]) -> str:
        text = _MENTION.sub(lambda m: f"@{self._resolve(m.group(1), users)}", text)
        text = _CHANNEL_REF.sub(r"#\1", text)
        text = _CHANNEL_REF_BARE.sub(r"#\1", text)
        text = _SPECIAL.sub(r"@\1", text)
        text = _LINK_LABELED.sub(r"\2 (\1)", text)
        text = _LINK_BARE.sub(r"\1", text)
        return html.unescape(text)

    def _episode(self, messages: list[SlackMessage], channel: str) -> Episode:
        first = messages[0]
        metadata: dict[str, Any] = {"source": "slack", "channel": channel}
        if len(messages) > 1 or (self.grouping == "message" and first.thread_ts):
            metadata["thread_ts"] = first.thread_ts or first.ts
        return Episode(
            data="\n".join(self.formatter(m) for m in messages),
            data_type="message",
            created_at=datetime.fromtimestamp(float(first.ts), tz=UTC).isoformat(),
            metadata=metadata,
            source_description=f"Slack #{channel} export",
        )
