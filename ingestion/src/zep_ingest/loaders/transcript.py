"""Speaker-labeled and WebVTT transcript exports to text episodes.

Each turn is rendered inline as ``Speaker: text`` so the speaker attribution
survives even though the episode is ingested as a plain-text episode.
"""

import glob
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from zep_ingest._validation import require_int_range
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode

_TIMESTAMP = re.compile(r"^\s*(?:\*\*)?\[?(\d{1,2}):(\d{2}):(\d{2})\]?(?:\*\*)?\s*$")
_VTT_TIME = r"(?:(?P<hours>\d{2,}):)?(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\.(?P<millis>\d{3})"
_VTT_CUE = re.compile(rf"^{_VTT_TIME}\s+-->\s+.+?(?:\s+\S+:\S+)*$")
_VTT_VOICE = re.compile(r"^<v(?:\.[^. >]+)*\s+([^>]+)>(.*)$", re.IGNORECASE)
_VTT_VOICE_END = re.compile(r"</v\s*>\s*$", re.IGNORECASE)
_BOLD_TURN = re.compile(r"^\*\*(?P<speaker>[^:*][^:]{0,80}?):?\*\*:?\s*(?P<text>.*)$")
_PLAIN_TURN = re.compile(r"^(?P<speaker>[A-Z][^:]{0,80}?):\s+(?P<text>.+)$")
_HEADER_LINE = re.compile(r"^([A-Z][A-Z _-]{2,30}):\s*(.*)$")
_HEADER_KEY_WORD = re.compile(r"[A-Z]+")
# Words that name transcript metadata rather than a person, matched against any
# word of a header key so ``MEETING ID`` and ``START TIME`` qualify too. Role
# words that double as speaker labels — HOST, SPEAKER, INTERVIEWER, SUBJECT,
# CALLER — are deliberately absent; see ``_match_header``.
_HEADER_KEYS = frozenset(
    """
    ATTENDEES CALL DATE DURATION LANGUAGE LOCATION MEETING PARTICIPANTS
    RECORDED RECORDING TIME TIMEZONE TITLE TOPIC TRANSCRIPT
    """.split()
)
_REDACTION_ONLY = re.compile(r"^\[[^\]]*\b(?:omitted|redacted)\b[^\]]*\]$", re.IGNORECASE)
_TRANSCRIPT_HEADING = re.compile(r"^#{1,6}\s.*transcript\s*$", re.IGNORECASE)
_DATE = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")

DEFAULT_CHUNK_CHARS = 3_500


@dataclass(slots=True)
class _Turn:
    speaker: str
    text: str
    offset: timedelta | None


class TranscriptLoader:
    def __init__(
        self,
        path_or_glob: str | Path,
        *,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        meeting_start: str | None = None,
        default_start_time: str | None = None,
    ) -> None:
        require_int_range("chunk_chars", chunk_chars, minimum=1)
        self.pattern = str(path_or_glob)
        self.chunk_chars = chunk_chars
        try:
            self._meeting_start = (
                datetime.fromisoformat(meeting_start) if meeting_start is not None else None
            )
        except ValueError as error:
            raise ConfigurationError(
                f"meeting_start must be an ISO datetime: {meeting_start!r}"
            ) from error
        if self._meeting_start is not None and self._meeting_start.tzinfo is None:
            raise ConfigurationError("meeting_start must include a timezone offset")
        try:
            self._default_start = (
                time.fromisoformat(default_start_time) if default_start_time else None
            )
        except ValueError as error:
            raise ConfigurationError(
                f"default_start_time must be an ISO time: {default_start_time!r}"
            ) from error
        if self._default_start is not None and self._default_start.tzinfo is None:
            raise ConfigurationError("default_start_time must include a timezone offset")
        self.warnings: list[str] = []
        self.files = sorted(
            Path(p) for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not self.files:
            raise ConfigurationError(f"No transcript files match {self.pattern!r}.")
        if meeting_start is not None and len(self.files) > 1:
            raise ConfigurationError("meeting_start can only be used with a single transcript")

    def load(self) -> Iterator[Episode]:
        for file in self.files:
            yield from self._load_file(file)

    def _load_file(self, file: Path) -> Iterator[Episode]:
        headers, lines = self._split_headers(file.read_text(encoding="utf-8"))
        turns = self._parse_turns(lines)
        if not turns:
            self.warnings.append(f"{file.name}: no speaker turns recognized; file skipped.")
            return
        start = self._resolve_start(file, headers)
        title = headers.get("MEETING") or headers.get("TITLE") or file.stem
        chunks = self._chunk(turns)
        for index, chunk in enumerate(chunks, start=1):
            created_at = None
            if start is not None:
                created_at = (start + (chunk[0].offset or timedelta())).isoformat()
            yield Episode(
                data="\n".join(f"{turn.speaker}: {turn.text}" for turn in chunk),
                data_type="text",
                created_at=created_at,
                metadata={
                    "source_type": "transcript",
                    "meeting": title[:100],
                    "chunk": f"{index}/{len(chunks)}",
                },
            )

    @staticmethod
    def _split_headers(text: str) -> tuple[dict[str, str], list[str]]:
        lines = text.splitlines()
        headers: dict[str, str] = {}
        body_start = 0
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped == "---":
                continue
            if _TIMESTAMP.match(stripped) or _VTT_CUE.match(stripped) or _BOLD_TURN.match(stripped):
                body_start = index
                break
            header = TranscriptLoader._match_header(stripped)
            if header:
                for piece in stripped.split(" | "):
                    match = _HEADER_LINE.match(piece.strip())
                    if match:
                        headers[match.group(1).strip()] = match.group(2).strip()
                body_start = index + 1
                continue
            body_start = index
            break
        body = lines[body_start:]
        for index, line in enumerate(body):
            if _TRANSCRIPT_HEADING.match(line.strip()):
                return headers, body[index + 1 :]
        return headers, body

    @staticmethod
    def _match_header(line: str) -> re.Match[str] | None:
        """The metadata match for a line, or None when it reads as a speaker turn.

        A header and an all-caps turn are the same shape — ``DATE: 2024-06-15``
        and ``ALICE: First turn`` both match :data:`_HEADER_LINE` — so the key
        has to settle it. A key with no value cannot be a turn, since
        :data:`_PLAIN_TURN` requires text after the colon; otherwise the key
        must name transcript metadata. The vocabulary stays narrow because the
        two mistakes are not symmetric: reading a turn as a header consumes the
        rest of the header block and can empty the file, while reading a header
        as a turn only leaves one extra line of text in the episode.
        """
        match = _HEADER_LINE.match(line)
        if match is None:
            return None
        if not match.group(2).strip():
            return match
        words = _HEADER_KEY_WORD.findall(match.group(1))
        return match if any(word in _HEADER_KEYS for word in words) else None

    def _parse_turns(self, lines: list[str]) -> list[_Turn]:
        turns: list[_Turn] = []
        offset: timedelta | None = None
        is_vtt = False
        for index, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            if line == "WEBVTT" and not turns:
                is_vtt = True
                continue
            if line.startswith("## "):
                break
            marker = _TIMESTAMP.match(line)
            if marker:
                hours, minutes, seconds = (int(value) for value in marker.groups())
                offset = timedelta(hours=hours, minutes=minutes, seconds=seconds)
                continue
            cue = _VTT_CUE.match(line)
            if cue:
                offset = timedelta(
                    hours=int(cue.group("hours") or 0),
                    minutes=int(cue.group("minutes")),
                    seconds=int(cue.group("seconds")),
                    milliseconds=int(cue.group("millis")),
                )
                continue
            if is_vtt and self._is_vtt_identifier(lines, index):
                continue
            if _REDACTION_ONLY.match(line):
                continue
            voice = _VTT_VOICE.match(line) if is_vtt else None
            if voice:
                text = _VTT_VOICE_END.sub("", voice.group(2)).strip()
                turns.append(_Turn(voice.group(1).strip(), text, offset))
                continue
            turn = _BOLD_TURN.match(line) or _PLAIN_TURN.match(line)
            if turn:
                turns.append(
                    _Turn(turn.group("speaker").strip(), turn.group("text").strip(), offset)
                )
            elif turns:
                continued = _VTT_VOICE_END.sub("", line) if is_vtt else line
                turns[-1].text = f"{turns[-1].text} {continued}".strip()
        return [turn for turn in turns if turn.text]

    @staticmethod
    def _is_vtt_identifier(lines: list[str], index: int) -> bool:
        """Whether this line is a cue identifier — the optional label above a cue.

        It qualifies only if it sits directly above the timing line, with no
        blank line between, *and* directly below the blank line that ends the
        previous cue. Both halves matter, and skipping blanks to find the timing
        line satisfies neither: the last payload line of a cue is also followed
        — after a blank — by the next cue's timing line, so it would be read as
        that cue's identifier and dropped. Since identifiers are optional, that
        empties all but the final turn of any file written without them.
        """
        if index + 1 >= len(lines) or not _VTT_CUE.match(lines[index + 1].strip()):
            return False
        if index == 0:
            return True
        previous = lines[index - 1].strip()
        return not previous or previous == "WEBVTT"

    def _resolve_start(self, file: Path, headers: dict[str, str]) -> datetime | None:
        if self._meeting_start is not None:
            return self._meeting_start
        date_match = _DATE.search(headers.get("DATE", "")) or _DATE.search(file.name)
        if date_match is None:
            return None
        if self._default_start is None:
            self.warnings.append(
                f"{file.name}: date found but no start time supplied; created_at left unset."
            )
            return None
        meeting_date = datetime.fromisoformat("-".join(date_match.groups())).date()
        start = datetime.combine(meeting_date, self._default_start)
        self.warnings.append(
            f"{file.name}: assumed meeting start {start.isoformat()} from default_start_time."
        )
        return start

    def _chunk(self, turns: list[_Turn]) -> list[list[_Turn]]:
        chunks: list[list[_Turn]] = []
        current: list[_Turn] = []
        size = 0
        for turn in turns:
            turn_size = len(turn.speaker) + len(turn.text) + 3
            if current and size + turn_size > self.chunk_chars:
                chunks.append(current)
                current, size = [], 0
            current.append(turn)
            size += turn_size
        if current:
            chunks.append(current)
        return chunks
