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
    assert episode.data_type == "message"
    assert episode.created_at == "2025-01-01T10:00:02+00:00"
    assert episode.data.splitlines() == [
        "Avery Brown: First generated turn.",
        "Blake Carter: Second generated turn.",
    ]


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
