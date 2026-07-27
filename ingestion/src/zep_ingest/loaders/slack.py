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


def _looks_like_handle(name: str) -> bool:
    """True for a single-token name ("morgan"), which reads as a Slack handle
    rather than a person. A bare first name fails to merge with the full name
    just as a handle does, so both are reported."""
    return not any(character.isspace() for character in name)


@dataclass(slots=True)
class SlackMessage:
    sender: str
    text: str
    ts: str
    channel: str  # the readable conversation label: a channel name, or DM members
    thread_ts: str | None = None
    conversation_type: ConversationType = "public_channel"
    # The raw Slack ID behind ``sender``, so a formatter= can substitute names
    # from its own directory when the export's roster is thin. None for a bot
    # post, which Slack writes with a username instead of a user id.
    user_id: str | None = None


@dataclass(slots=True)
class _Conversation:
    """One conversation in the export: the folder to read, what to call it, its type."""

    folder: str
    label: str
    kind: ConversationType
    # roster ids rendered into ``label`` (DMs and group DMs only). The label names
    # them in every episode without going through _resolve, so they are carried
    # here and recorded once the conversation is known to be both selected and
    # non-empty — a skipped conversation must not warn about its members.
    member_ids: tuple[str, ...] = ()


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

    def close(self) -> None:
        """No handle is held open; defined so both readers share one interface."""


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

    def close(self) -> None:
        self.zip.close()


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
        # roster ids whose best name is not a person's full name, and the subset
        # of those actually used by ingested content (id -> the name used)
        self._weak_name_ids: frozenset[str] = frozenset()
        self._weak_names: dict[str, str] = {}
        # weak names seen while parsing one message, promoted to _weak_names only
        # once that message survives validation (see _resolve)
        self._pending_weak_names: dict[str, str] = {}
        self._duplicate_ts = 0
        self._invalid_ts = 0

    def load(self) -> Iterator[Episode]:
        reader = _ZipReader(self.path) if self.path.is_file() else _DirReader(self.path)
        # both reset per pass: a second load() re-derives them, and appending to
        # the previous pass's list would report every warning twice
        self._unresolved_users = set()
        self._weak_names = {}
        self._pending_weak_names = {}
        self._duplicate_ts = 0
        self._invalid_ts = 0
        self.warnings = []
        roster = self._read_roster(reader)
        users, self._weak_name_ids = self._user_map(roster)
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
        try:
            for conversation in conversations:
                yield from self._load_conversation(reader, conversation, users)
        finally:
            # a caller that stops early — preview(limit=...) — abandons this
            # generator at a yield, so both the archive and the tallies below are
            # handled here: anything after the try block would never run, and a
            # preview that saw a problem has to be able to report it
            reader.close()
            self._summarize()

    def _summarize(self) -> None:
        """Append the counts gathered while reading. On an abandoned generator
        these describe only the messages actually consumed, which is what a
        partial preview should report."""
        if self._unresolved_users:
            self.warnings.append(
                f"{len(self._unresolved_users)} Slack user ID(s) referenced in "
                "messages were absent from the roster (typically deactivated, bot, "
                "or Slack Connect users) and were left as raw IDs."
            )
        if self._weak_names:
            examples = ", ".join(sorted(self._weak_names.values())[:3])
            self.warnings.append(
                f"{len(self._weak_names)} Slack user(s) named in ingested content have "
                f"no real_name in the roster, so they are labeled with a display-name "
                f"handle, a username, or a raw ID instead (e.g. {examples}). Zep merges "
                "entities by the names it sees, so these may not merge with the same "
                "person written in full in another source. Populate real_name in the "
                "export, or pass formatter= and map SlackMessage.user_id to your own names."
            )
        if self._invalid_ts:
            self.warnings.append(
                f"{self._invalid_ts} Slack message(s) had a timestamp that is not a "
                "number and were skipped; Slack writes ts as epoch seconds, so an "
                "export carrying anything else has been altered or is corrupt."
            )
        if self._duplicate_ts:
            self.warnings.append(
                f"{self._duplicate_ts} Slack message(s) repeated a timestamp already "
                "seen in the same conversation and were skipped as duplicates. Exports "
                "merged from several dumps can repeat messages; verify the export if "
                "you did not expect this."
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
    def _user_map(roster: Any) -> tuple[dict[str, str], frozenset[str]]:
        """Map each Slack user ID to the best name the roster offers.

        ``real_name`` is preferred over ``display_name``. Zep merges entities by
        the names it sees in text, and a Slack display name is frequently a short
        handle ("morgan") that will not merge with the same person written in full
        ("Morgan Lee") in an email or document, splitting one person into two
        nodes. Slack's own precedence is the opposite, but it optimizes for how a
        name reads in a chat client, not for entity resolution.

        Returns the mapping plus the IDs whose name is *not* a person's full name,
        so the run can warn about the ones it actually used.
        """
        mapping: dict[str, str] = {}
        weak: set[str] = set()
        for user in roster or []:
            profile = user.get("profile") or {}
            real_name = (profile.get("real_name") or "").strip()
            display_name = (profile.get("display_name") or "").strip()
            username = (user.get("name") or "").strip()
            if real_name:
                name = real_name
            elif display_name:
                name = display_name
                if _looks_like_handle(display_name):
                    weak.add(user["id"])
            elif username:
                # a username slug ("morgan.lee") is a poor entity name
                name = username
                weak.add(user["id"])
            else:
                name = user["id"]
                weak.add(user["id"])
            mapping[user["id"]] = name
        return mapping, frozenset(weak)

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
                label, member_ids = self._label(entry, folder, kind, users)
                conversations.append(_Conversation(folder, label, kind, member_ids))
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
    ) -> tuple[str, tuple[str, ...]]:
        """Channels are labeled by name; DMs and group DMs by their members, since
        their folders are an opaque id or slug and raw ids degrade extraction.

        Returns the label and the roster ids it names, so the caller can report a
        member whose name is only a handle even if they never posted.
        """
        if kind not in ("dm", "group_dm"):
            return folder, ()
        members = [m for m in entry.get("members") or [] if isinstance(m, str)]
        if not members:
            return folder, ()
        return ", ".join(users.get(member, member) for member in members), tuple(members)

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
                if message is None:
                    continue
                if message.ts in seen_ts:
                    self._duplicate_ts += 1
                    continue
                seen_ts.add(message.ts)
                messages.append(message)
                # accepted, so the names its text carries really do reach the graph
                self._weak_names.update(self._pending_weak_names)
        messages.sort(key=lambda m: float(m.ts))
        if messages:
            # every episode below carries conversation.label, so the members it
            # names are now in the graph whether or not they authored anything
            self._note_label_names(conversation, users)
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
        # @mentions are resolved while normalizing text below, which happens before
        # this message is known to be usable; buffer what that records so a message
        # dropped further down does not claim its mentions reached the graph
        self._pending_weak_names = {}
        if raw.get("subtype") in self.skip_subtypes:
            return None
        # bot_message is the subtype Slack gives an app post; most carry a bot_id
        # too, but an incoming-webhook post often has only the subtype, so keying
        # solely on bot_id lets that traffic through include_bots=False
        if not self.include_bots and (raw.get("bot_id") or raw.get("subtype") == "bot_message"):
            return None
        text = self._normalize_text(raw.get("text") or "", users).strip()
        if not text:
            return None
        ts = raw.get("ts")
        if ts is None:
            return None
        # ordering and created_at both read ts as a float later, where a bad value
        # would abort the whole export; drop the one message and report it instead
        try:
            float(ts)
        except (TypeError, ValueError):
            self._invalid_ts += 1
            return None
        if raw.get("thread_ts") is not None:
            try:
                float(raw["thread_ts"])
            except (TypeError, ValueError):
                self._invalid_ts += 1
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
            user_id=raw.get("user") or None,
        )

    def _note_label_names(self, conversation: _Conversation, users: dict[str, str]) -> None:
        """Record weak names a DM label puts into the graph. Called only for a
        selected conversation that yielded episodes, so members of a conversation
        the run skipped are never reported."""
        for member in conversation.member_ids:
            if member in self._weak_name_ids:
                self._weak_names[member] = users[member]

    def _resolve(self, user_id: str, users: dict[str, str]) -> str:
        """Map a Slack user ID to a name, recording IDs the roster misses and the
        names that are not a person's full name.

        The two are recorded at different times on purpose, because they claim
        different things: an unresolved id was "referenced in messages", which
        holds even for a message this run goes on to drop, while a weak name is
        reported as "named in ingested content", which does not. Weak names
        therefore go to a per-message buffer that _load_conversation promotes only
        once the message is accepted.
        """
        name = users.get(user_id)
        if name is None:
            self._unresolved_users.add(user_id)
            return user_id
        if user_id in self._weak_name_ids:
            self._pending_weak_names[user_id] = name
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
        # a lone message still belongs to a thread when it carries a thread_ts —
        # its parent may have been filtered out as a join or bot message — so
        # message count alone would leave that episode unfilterable by thread
        if len(messages) > 1 or first.thread_ts:
            metadata["thread_ts"] = first.thread_ts or first.ts
        return Episode(
            data="\n".join(self.formatter(m) for m in messages),
            data_type="text",
            created_at=datetime.fromtimestamp(float(first.ts), tz=UTC).isoformat(),
            metadata=metadata,
        )
