"""SlackExportLoader: standard Slack workspace exports → text episodes.

Accepts an export .zip (read in place) or an extracted directory. A Slack
export indexes its conversations across four files — channels.json (public
channels), groups.json (private channels), dms.json (1:1 DMs) and mpims.json
(group DMs) — and all four are read. ``conversation_types`` picks which of
them are ingested; it defaults to public channels only, and anything found in
the export but not selected is reported in ``warnings`` rather than dropped
silently. An export carrying none of those indexes is typed by folder name
instead: DM and group DM folders are recognized and stay excluded by default,
and the rest are read as channels with a warning that their types could not be
confirmed. Resolves user IDs to display names via the export's users.json — or
org_users.json in an Enterprise Grid organization export — and warns when that
roster is missing or does not cover every referenced user (either case leaves
raw Slack IDs in the graph, which degrades entity extraction); the same roster
labels DMs and group DMs by their members, since Slack names those folders
with an opaque id (D01ABC234) or slug (mpdm-alice--bob-1). Normalizes Slack
markup, skips join/leave/bot noise, groups messages by thread (the semantic
unit Zep extracts best from), and stamps every episode with the original
message timestamp so backfilled facts carry the correct valid_at timeline.
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

ConversationType = Literal["public_channel", "private_channel", "dm", "group_dm"]

# the four conversation indexes a Slack export ships, in reporting order
CONVERSATION_FILES: dict[ConversationType, str] = {
    "public_channel": "channels.json",
    "private_channel": "groups.json",
    "dm": "dms.json",
    "group_dm": "mpims.json",
}

# the user roster: users.json in a standard export, org_users.json in an
# Enterprise Grid organization export
ROSTER_FILES: tuple[str, ...] = ("users.json", "org_users.json")

# any of these at the root identifies an export root, which is what lets a
# single folder wrapping the export be recognized and stripped
EXPORT_MARKER_FILES: frozenset[str] = frozenset({*CONVERSATION_FILES.values(), *ROSTER_FILES})

# public channels only: DMs must never start flowing into a graph by default
DEFAULT_CONVERSATION_TYPES: tuple[ConversationType, ...] = ("public_channel",)

_CONVERSATION_NOUNS: dict[ConversationType, str] = {
    "public_channel": "public channel(s)",
    "private_channel": "private channel(s)",
    "dm": "DM conversation(s)",
    "group_dm": "group DM conversation(s)",
}

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

# Slack names a DM folder with the conversation's opaque id (D01ABC234) and a
# group DM folder with an mpdm- slug; channel names are lowercase, so a channel
# folder cannot take either shape
_DM_FOLDER = re.compile(r"^D[A-Z0-9]{2,}$")
_GROUP_DM_FOLDER = re.compile(r"^mpdm-")

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
    channel: str  # the readable conversation label: a channel name, or DM members
    thread_ts: str | None = None
    conversation_type: ConversationType = "public_channel"


@dataclass(slots=True)
class _Conversation:
    """One conversation in the export: the folder to read, what to call it, its type."""

    folder: str
    label: str
    kind: ConversationType


class _DirReader:
    def __init__(self, path: Path) -> None:
        self.path = self._unwrap(path.resolve())

    @staticmethod
    def _unwrap(root: Path) -> Path:
        """Re-root onto a single top-level folder wrapping the export.

        An export stays an export once it is extracted, so this mirrors the zip
        reader: without it, the wrapper reads as one conversation whose only
        files are the index files, and a valid export ingests nothing.
        """
        if any((root / marker).is_file() for marker in EXPORT_MARKER_FILES):
            return root
        children = [child for child in root.iterdir() if child.is_dir()]
        if len(children) != 1:
            return root
        candidate = children[0].resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return root  # a symlink out of the export is not a wrapper
        if any((candidate / marker).is_file() for marker in EXPORT_MARKER_FILES):
            return candidate
        return root

    def _resolve_export_path(self, relpath: str) -> Path:
        """Resolve a path and reject traversal or symlinks outside the export."""
        candidate = (self.path / relpath).resolve()
        try:
            candidate.relative_to(self.path)
        except ValueError as error:
            raise ConfigurationError(f"Slack export path escapes its root: {relpath!r}") from error
        return candidate

    def read_json(self, relpath: str) -> Any | None:
        file = self._resolve_export_path(relpath)
        if not file.exists():
            return None
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as error:
            raise ConfigurationError(f"Unparseable JSON in Slack export: {relpath}") from error

    def channel_dirs(self) -> list[str]:
        directories: list[str] = []
        for path in self.path.iterdir():
            if path.is_dir():
                self._resolve_export_path(path.name)
                directories.append(path.name)
        return sorted(directories)

    def day_files(self, channel: str) -> list[str]:
        """A conversation's day files: the .json files directly inside its folder."""
        directory = self._resolve_export_path(channel)
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.glob("*.json") if p.is_file())


class _ZipReader:
    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path)
        names = self.zip.namelist()
        entries = set(names)
        # tolerate a single top-level folder wrapping the export: any index or
        # roster file marks the real root, and an export missing channels.json
        # or naming its roster org_users.json is still an export
        self.prefix = ""
        if names and not (EXPORT_MARKER_FILES & entries):
            roots = {name.split("/", 1)[0] for name in names if "/" in name}
            if len(roots) == 1:
                candidate = next(iter(roots)) + "/"
                if any(candidate + marker in entries for marker in EXPORT_MARKER_FILES):
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
        """A conversation's day files: the .json files directly inside its folder.

        Anything deeper belongs to another conversation or to an attachment
        folder, and reading it here would file one conversation's messages under
        another's name and type.
        """
        prefix = channel + "/"
        return sorted(
            name[len(prefix) :]
            for name in self.names
            if name.startswith(prefix) and name.endswith(".json") and "/" not in name[len(prefix) :]
        )


def _conversation_ref(message: SlackMessage) -> str:
    if message.conversation_type == "dm":
        return f"DM: {message.channel}"
    if message.conversation_type == "group_dm":
        return f"group DM: {message.channel}"
    return f"#{message.channel}"


def _default_formatter(message: SlackMessage) -> str:
    stamp = datetime.fromtimestamp(float(message.ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
    return f"{message.sender} (Slack {_conversation_ref(message)}, {stamp} UTC): {message.text}"


def _matches(conversation: _Conversation, name: str) -> bool:
    """channels= names a conversation by its export identity (channel name, mpdm
    slug or DM id) or by the member label DMs are resolved to."""
    return name in (conversation.folder, conversation.label)


def _folder_kind(folder: str) -> ConversationType:
    """Type a conversation folder by its name, for an export carrying no indexes."""
    if _GROUP_DM_FOLDER.match(folder):
        return "group_dm"
    if _DM_FOLDER.match(folder):
        return "dm"
    return "public_channel"


def _describe(conversation: _Conversation) -> str:
    if conversation.label == conversation.folder:
        return conversation.folder
    return f"{conversation.folder} ({conversation.label})"


class SlackExportLoader:
    def __init__(
        self,
        path: str | Path,
        *,
        channels: Sequence[str] | None = None,
        conversation_types: Sequence[ConversationType] = DEFAULT_CONVERSATION_TYPES,
        grouping: Literal["thread", "message"] = "thread",
        include_bots: bool = False,
        skip_subtypes: frozenset[str] = DEFAULT_SKIP_SUBTYPES,
        formatter: Callable[[SlackMessage], str] | None = None,
    ) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise ConfigurationError(f"Slack export not found: {self.path}")
        if grouping not in ("thread", "message"):
            raise ConfigurationError(f"grouping must be 'thread' or 'message', got {grouping!r}")
        self.conversation_types = tuple(dict.fromkeys(conversation_types))
        if not self.conversation_types or any(
            kind not in CONVERSATION_FILES for kind in self.conversation_types
        ):
            raise ConfigurationError(
                "conversation_types must be a non-empty subset of "
                f"{list(CONVERSATION_FILES)}, got {conversation_types!r}"
            )
        # channels= selects by name *within* the selected conversation types
        self.channels = list(channels) if channels is not None else None
        self.grouping = grouping
        self.include_bots = include_bots
        self.skip_subtypes = skip_subtypes
        self.formatter = formatter or _default_formatter
        self.warnings: list[str] = []
        self._unresolved_users: set[str] = set()

    def load(self) -> Iterator[Episode]:
        reader = _ZipReader(self.path) if self.path.is_file() else _DirReader(self.path)
        # both reset per pass: a second load() re-derives them, and appending to
        # the previous pass's list would report every warning twice
        self._unresolved_users = set()
        self.warnings = []
        roster = self._read_roster(reader)
        users = self._user_map(roster)
        inventory = self._inventory(reader, users)
        if roster is None and not inventory:
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
        conversations = self._select(inventory)
        self._warn_skipped_types(inventory)
        for conversation in conversations:
            yield from self._load_conversation(reader, conversation, users)
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
        for filename in ROSTER_FILES:
            roster = reader.read_json(filename)
            if roster is not None:
                return roster
        return None

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

    def _inventory(
        self, reader: _DirReader | _ZipReader, users: dict[str, str]
    ) -> list[_Conversation]:
        """Every conversation the export declares, across all four indexes.

        Only an export carrying none of them falls back to typing its folders by
        name, which is all an index-less export gives you to go on.
        """
        conversations: list[_Conversation] = []
        seen: set[str] = set()
        for kind, filename in CONVERSATION_FILES.items():
            for entry in reader.read_json(filename) or []:
                if not isinstance(entry, dict):
                    raise ConfigurationError(f"Invalid Slack channel path in export: {entry!r}")
                # dms.json entries carry no name — the folder is the DM id
                folder = self._validated_folder(entry.get("name") or entry.get("id"))
                if folder in seen:
                    continue
                seen.add(folder)
                conversations.append(
                    _Conversation(folder, self._label(entry, folder, kind, users), kind)
                )
        if conversations:
            return conversations
        return self._folder_inventory(reader)

    def _folder_inventory(self, reader: _DirReader | _ZipReader) -> list[_Conversation]:
        """An index-less export: folder names are the only evidence of type.

        Slack names DM and group DM folders unmistakably, so those are typed from
        the folder name and stay behind the default conversation_types. Nothing
        distinguishes a private channel's folder from a public one without
        groups.json, so the rest are read as public channels — an assumption that
        is warned about rather than made silently.
        """
        inventory = [
            _Conversation(folder, folder, _folder_kind(folder))
            for folder in (self._validated_folder(d) for d in reader.channel_dirs())
        ]
        assumed = sum(1 for c in inventory if c.kind == "public_channel")
        if assumed:
            self.warnings.append(
                "The Slack export declares no conversations (it has no channels.json, "
                "groups.json, dms.json or mpims.json), so conversation types could not "
                f"be determined: {assumed} folder(s) were read as public channels. A "
                "private channel's folder is indistinguishable from a public one in an "
                "index-less export, so confirm none of them are private before "
                "trusting what lands in the graph."
            )
        return inventory

    @staticmethod
    def _validated_folder(folder: Any) -> str:
        """Reject anything that could escape the export root, from any index."""
        if (
            not isinstance(folder, str)
            or not folder
            or folder in {".", ".."}
            or "/" in folder
            or "\\" in folder
        ):
            raise ConfigurationError(f"Invalid Slack channel path in export: {folder!r}")
        return folder

    @staticmethod
    def _label(
        entry: dict[str, Any], folder: str, kind: ConversationType, users: dict[str, str]
    ) -> str:
        """Channels are labeled by name; DMs and group DMs by their members, since
        their folders are an opaque id or slug and raw ids degrade extraction."""
        if kind not in ("dm", "group_dm"):
            return folder
        members = [m for m in entry.get("members") or [] if isinstance(m, str)]
        if not members:
            return folder
        return ", ".join(users.get(member, member) for member in members)

    def _select(self, inventory: list[_Conversation]) -> list[_Conversation]:
        """conversation_types picks the types; channels= filters by name within them."""
        selected = [c for c in inventory if c.kind in self.conversation_types]
        if self.channels is None:
            return selected
        missing = [name for name in self.channels if not any(_matches(c, name) for c in inventory)]
        if missing:
            raise ConfigurationError(
                f"Channel(s) not present in the Slack export: {', '.join(missing)}. "
                f"Available: {', '.join(_describe(c) for c in inventory)}."
            )
        excluded = [name for name in self.channels if not any(_matches(c, name) for c in selected)]
        if excluded:
            kinds = sorted({c.kind for name in excluded for c in inventory if _matches(c, name)})
            raise ConfigurationError(
                f"Channel(s) in the Slack export excluded by conversation_types="
                f"{list(self.conversation_types)}: {', '.join(excluded)}. Add "
                f"{', '.join(kinds)} to conversation_types to ingest them."
            )
        return [c for c in selected if any(_matches(c, name) for name in self.channels)]

    def _warn_skipped_types(self, inventory: list[_Conversation]) -> None:
        """What the export holds, by index or by folder name, is never dropped in silence."""
        skipped = [
            (kind, sum(1 for c in inventory if c.kind == kind))
            for kind in CONVERSATION_FILES
            if kind not in self.conversation_types
        ]
        found = [(kind, count) for kind, count in skipped if count]
        if not found:
            return
        counted = ", ".join(f"{count} {_CONVERSATION_NOUNS[kind]}" for kind, count in found)
        with_skipped = list(self.conversation_types) + [kind for kind, _ in found]
        self.warnings.append(
            f"The Slack export contains {counted} that were not ingested: "
            f"conversation_types={list(self.conversation_types)} does not select "
            f"them. Pass conversation_types={with_skipped} to include them."
        )

    def _load_conversation(
        self, reader: _DirReader | _ZipReader, conversation: _Conversation, users: dict[str, str]
    ) -> Iterator[Episode]:
        messages: list[SlackMessage] = []
        seen_ts: set[str] = set()
        for day_file in reader.day_files(conversation.folder):
            raw_messages = reader.read_json(f"{conversation.folder}/{day_file}") or []
            for raw in raw_messages:
                message = self._parse(raw, conversation, users)
                if message is None or message.ts in seen_ts:
                    continue
                seen_ts.add(message.ts)
                messages.append(message)
        messages.sort(key=lambda m: float(m.ts))
        if self.grouping == "message":
            for message in messages:
                yield self._episode([message], conversation)
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
            yield self._episode(threads[key], conversation)

    def _parse(
        self, raw: dict[str, Any], conversation: _Conversation, users: dict[str, str]
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
            channel=conversation.label,
            thread_ts=raw.get("thread_ts"),
            conversation_type=conversation.kind,
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

    def _episode(self, messages: list[SlackMessage], conversation: _Conversation) -> Episode:
        first = messages[0]
        metadata: dict[str, Any] = {
            "source_type": "slack",
            "channel": conversation.label,
            "conversation_type": conversation.kind,
        }
        if len(messages) > 1 or (self.grouping == "message" and first.thread_ts):
            metadata["thread_ts"] = first.thread_ts or first.ts
        return Episode(
            data="\n".join(self.formatter(m) for m in messages),
            data_type="text",
            created_at=datetime.fromtimestamp(float(first.ts), tz=UTC).isoformat(),
            metadata=metadata,
        )
