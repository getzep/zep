"""Tests for TextFileLoader."""

from datetime import UTC, datetime

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.text import TextFileLoader


def test_one_text_episode_per_file(tmp_path):
    (tmp_path / "a.md").write_text("Document A")
    (tmp_path / "b.md").write_text("Document B")
    episodes = list(TextFileLoader(str(tmp_path / "*.md")).load())
    assert sorted(e.data for e in episodes) == ["Document A", "Document B"]
    for ep in episodes:
        assert ep.data_type == "text"
        assert ep.source_description in ("a.md", "b.md")


def test_created_at_from_mtime(tmp_path):
    file = tmp_path / "doc.txt"
    file.write_text("content")
    [episode] = TextFileLoader(str(file)).load()
    expected = datetime.fromtimestamp(file.stat().st_mtime, tz=UTC).isoformat()
    assert episode.created_at == expected


def test_created_at_override(tmp_path):
    (tmp_path / "doc.txt").write_text("content")
    [episode] = TextFileLoader(str(tmp_path / "doc.txt"), created_at="2020-01-01T00:00:00Z").load()
    assert episode.created_at == "2020-01-01T00:00:00Z"


def test_no_matches_raises_eagerly(tmp_path):
    with pytest.raises(ConfigurationError):
        TextFileLoader(str(tmp_path / "*.nope"))


def test_recursive_glob(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.md").write_text("Deep")
    episodes = list(TextFileLoader(str(tmp_path / "**/*.md")).load())
    assert [e.data for e in episodes] == ["Deep"]
