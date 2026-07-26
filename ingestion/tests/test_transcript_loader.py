"""Standards-oriented tests for the public transcript loader."""

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.transcript import TranscriptLoader


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


def test_speaker_transcript_chunks_at_turn_boundaries(tmp_path):
    path = write(
        tmp_path,
        "meeting.txt",
        "MEETING: Quarterly Review\n"
        "00:00:02\n"
        "Avery Brown: First generated turn.\n"
        "Blake Carter: Second generated turn.\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert episode.data_type == "text"
    assert episode.created_at == "2025-01-01T10:00:02+00:00"
    assert episode.metadata["source_type"] == "transcript"
    assert episode.metadata["meeting"] == "Quarterly Review"
    assert episode.data.splitlines() == [
        "Avery Brown: First generated turn.",
        "Blake Carter: Second generated turn.",
    ]


def test_all_caps_speaker_turns_are_not_read_as_headers(tmp_path):
    """An all-caps turn is shaped exactly like a ``KEY: value`` metadata header.
    Reading it as one consumes the turns as metadata and leaves nothing to ingest."""
    path = write(
        tmp_path,
        "meeting.txt",
        "AVERY BROWN: First generated turn.\n"
        "BLAKE CARTER: Second generated turn.\n"
        "AVERY BROWN: Third generated turn.\n",
    )
    loader = TranscriptLoader(path)
    [episode] = loader.load()
    assert loader.warnings == []
    assert episode.data.splitlines() == [
        "AVERY BROWN: First generated turn.",
        "BLAKE CARTER: Second generated turn.",
        "AVERY BROWN: Third generated turn.",
    ]


@pytest.mark.parametrize("separator", ["", "\n"])
def test_all_caps_speakers_keep_their_turns_alongside_real_headers(tmp_path, separator):
    """Metadata keys are still stripped, and still supply the date, whether or not a
    blank line separates the header block from the first turn."""
    path = write(
        tmp_path,
        "meeting.txt",
        "MEETING: Quarterly Review\n"
        "DATE: 2025-01-01\n"
        "PARTICIPANTS: Avery Brown, Blake Carter\n"
        f"{separator}"
        "AVERY BROWN: First generated turn.\n"
        "BLAKE CARTER: Second generated turn.\n",
    )
    [episode] = TranscriptLoader(path, default_start_time="09:00:00+00:00").load()
    assert episode.created_at == "2025-01-01T09:00:00+00:00"
    assert episode.metadata["meeting"] == "Quarterly Review"
    assert episode.data.splitlines() == [
        "AVERY BROWN: First generated turn.",
        "BLAKE CARTER: Second generated turn.",
    ]


def test_valueless_key_does_not_end_the_header_block(tmp_path):
    """A key with nothing after the colon cannot be a turn, so the scan continues and
    the ``DATE`` below it is still read."""
    path = write(
        tmp_path,
        "meeting.txt",
        "NOTES:\nDATE: 2025-01-01\nAVERY BROWN: First generated turn.\n",
    )
    [episode] = TranscriptLoader(path, default_start_time="09:00:00+00:00").load()
    assert episode.created_at == "2025-01-01T09:00:00+00:00"
    assert episode.data.splitlines() == ["AVERY BROWN: First generated turn."]


def test_webvtt_optional_hours_identifier_settings_voice_and_millis(tmp_path):
    path = write(
        tmp_path,
        "meeting.vtt",
        "WEBVTT\n\n"
        "generated-cue-a\n"
        "00:02.125 --> 00:04.000 align:start position:10%\n"
        "<v.panel.host Avery Brown>Generated opening.\n\n"
        "generated-cue-b\n"
        "01:02:03.500 --> 01:02:04.000 line:20%\n"
        "<v Blake Carter>Generated response.\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert "generated-cue" not in episode.data
    assert "align:start" not in episode.data
    assert episode.data.splitlines() == [
        "Avery Brown: Generated opening.",
        "Blake Carter: Generated response.",
    ]
    assert episode.created_at == "2025-01-01T10:00:02.125000+00:00"


def test_webvtt_without_cue_identifiers_keeps_every_turn(tmp_path):
    """Cue identifiers are optional. When a file omits them, each cue's payload is
    still followed — after a blank line — by the next cue's timing line, which must
    not be read as an identifier: doing so drops every turn but the last."""
    path = write(
        tmp_path,
        "no_identifiers.vtt",
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:03.000\n"
        "<v Avery Brown>First line.\n\n"
        "00:00:03.000 --> 00:00:06.000\n"
        "<v Blake Carter>Second line.\n\n"
        "00:00:06.000 --> 00:00:09.000\n"
        "<v Avery Brown>Third line.\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert episode.data.splitlines() == [
        "Avery Brown: First line.",
        "Blake Carter: Second line.",
        "Avery Brown: Third line.",
    ]


def test_webvtt_payload_abutting_the_next_cue_is_not_an_identifier(tmp_path):
    """An identifier also has to open its block. Without that check, a payload line
    left flush against the next timing line would be dropped as an identifier."""
    path = write(
        tmp_path,
        "no_blank_line.vtt",
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:03.000\n"
        "<v Avery Brown>First line.\n"
        "00:00:03.000 --> 00:00:06.000\n"
        "<v Blake Carter>Second line.\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert episode.data.splitlines() == [
        "Avery Brown: First line.",
        "Blake Carter: Second line.",
    ]


def test_webvtt_numeric_cue_identifiers_are_still_dropped(tmp_path):
    """The common generated form: identifiers that are bare sequence numbers."""
    path = write(
        tmp_path,
        "numbered.vtt",
        "WEBVTT\n\n"
        "1\n"
        "00:00:00.000 --> 00:00:03.000\n"
        "<v Avery Brown>First line.\n\n"
        "2\n"
        "00:00:03.000 --> 00:00:06.000\n"
        "<v Blake Carter>Second line.\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert episode.data.splitlines() == [
        "Avery Brown: First line.",
        "Blake Carter: Second line.",
    ]


def test_webvtt_voice_end_tags_are_stripped(tmp_path):
    path = write(
        tmp_path,
        "meeting.vtt",
        "WEBVTT\n\n"
        "generated-cue-a\n"
        "00:00:02.000 --> 00:00:04.000\n"
        "<v Avery Brown>Generated opening.</v>\n\n"
        "generated-cue-b\n"
        "00:00:04.000 --> 00:00:06.000\n"
        "<v.panel.host Blake Carter>Generated response.</v>   \n\n"
        "generated-cue-c\n"
        "00:00:06.000 --> 00:00:08.000\n"
        "<v Casey Diaz>Generated follow-up.</V>\n",
    )
    [episode] = TranscriptLoader(path, meeting_start="2025-01-01T10:00:00Z").load()
    assert "</v>" not in episode.data
    assert "</V>" not in episode.data
    assert episode.data.splitlines() == [
        "Avery Brown: Generated opening.",
        "Blake Carter: Generated response.",
        "Casey Diaz: Generated follow-up.",
    ]
    assert episode.created_at == "2025-01-01T10:00:02+00:00"


def test_webvtt_voice_end_tag_closing_a_wrapped_cue_is_stripped(tmp_path):
    path = write(
        tmp_path,
        "meeting.vtt",
        "WEBVTT\n\n"
        "generated-cue-a\n"
        "00:00:02.000 --> 00:00:06.000\n"
        "<v Avery Brown>Generated opening\n"
        "that wraps onto a second line.</v>\n",
    )
    [episode] = TranscriptLoader(path).load()
    assert episode.data.splitlines() == [
        "Avery Brown: Generated opening that wraps onto a second line.",
    ]


def test_webvtt_voice_end_tag_inside_cue_text_is_preserved(tmp_path):
    path = write(
        tmp_path,
        "meeting.vtt",
        "WEBVTT\n\n"
        "generated-cue-a\n"
        "00:00:02.000 --> 00:00:04.000\n"
        "<v Avery Brown>The </v> tag closes a voice span.\n",
    )
    [episode] = TranscriptLoader(path).load()
    assert episode.data.splitlines() == ["Avery Brown: The </v> tag closes a voice span."]


def test_stage_direction_preserved_but_redaction_removed(tmp_path):
    path = write(
        tmp_path,
        "meeting.txt",
        "Avery Brown: Generated opening.\n[inaudible]\n[personal note redacted]\n",
    )
    [episode] = TranscriptLoader(path).load()
    assert "[inaudible]" in episode.data
    assert "redacted" not in episode.data


def test_date_only_does_not_invent_time(tmp_path):
    path = write(tmp_path, "meeting_2025-01-01.txt", "Avery Brown: Review started.\n")
    loader = TranscriptLoader(path)
    [episode] = loader.load()
    assert episode.created_at is None
    assert any("no start time" in warning for warning in loader.warnings)


def test_explicit_default_time_is_opt_in(tmp_path):
    path = write(tmp_path, "meeting_2025-01-01.txt", "Avery Brown: Review started.\n")
    loader = TranscriptLoader(path, default_start_time="12:00:00+00:00")
    [episode] = loader.load()
    assert episode.created_at == "2025-01-01T12:00:00+00:00"


@pytest.mark.parametrize("chunk_chars", [0, -1])
def test_invalid_chunk_size_rejected(tmp_path, chunk_chars):
    path = write(tmp_path, "meeting.txt", "Avery Brown: Review started.\n")
    with pytest.raises(ConfigurationError, match="chunk_chars"):
        TranscriptLoader(path, chunk_chars=chunk_chars)
