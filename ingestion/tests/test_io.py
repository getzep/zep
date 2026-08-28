"""Tests for JSON row-file parsing and field preservation."""

import json

import pytest

from zep_ingest._io import load_rows, resolve_source_files, rows_to_fields
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.threads import ThreadMessage


def test_pretty_printed_single_json_object_is_one_row(tmp_path):
    path = tmp_path / "message.json"
    path.write_text(json.dumps({"thread_id": "t1", "content": "hello"}, indent=2))

    assert load_rows(path) == [{"thread_id": "t1", "content": "hello"}]


@pytest.mark.parametrize("content", ["", "   \n\n  "])
def test_empty_file_is_rejected_rather_than_ingesting_nothing(tmp_path, content):
    """An empty file parses as zero JSONL rows, so without this the run reports
    success having submitted nothing — nearly always the wrong path."""
    path = tmp_path / "messages.jsonl"
    path.write_text(content)

    with pytest.raises(ConfigurationError, match="is empty"):
        load_rows(path)


def test_explicitly_empty_array_is_allowed(tmp_path):
    # "[]" says ingesting nothing is deliberate, unlike a blank file
    path = tmp_path / "messages.json"
    path.write_text("[]")

    assert load_rows(path) == []


def test_empty_fields_are_preserved_for_dataclass_validation():
    # a present-but-empty value is not a missing column: it reaches the
    # dataclass, which is what names the field in the error
    row = {
        "thread_id": "t1",
        "role": "",
        "name": "Avery Brown",
        "content": "hello",
        "created_at": "2024-06-15T10:30:00Z",
    }

    # the optional column (metadata) is absent, which must not trip the
    # required-field check
    assert rows_to_fields([row], ThreadMessage) == [row]


def test_json_scalar_is_rejected_with_clear_error(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text("42")

    with pytest.raises(ConfigurationError, match="must contain JSON objects"):
        load_rows(path)


def test_resolve_source_files_sequence_preserves_order_and_dedupes(tmp_path):
    first = tmp_path / "issues.jsonl"
    second = tmp_path / "prs.jsonl"
    first.write_text("{}\n")
    second.write_text("{}\n")

    files = resolve_source_files([second, first, second])

    assert files == [second, first]


def test_resolve_source_files_empty_sequence_is_rejected():
    with pytest.raises(ConfigurationError, match="No files were provided"):
        resolve_source_files([])
