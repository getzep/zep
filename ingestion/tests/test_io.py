"""Tests for JSON row-file parsing and field preservation."""

import json

import pytest

from zep_ingest._io import load_rows, rows_to_fields
from zep_ingest.exceptions import ConfigurationError


def test_pretty_printed_single_json_object_is_one_row(tmp_path):
    path = tmp_path / "message.json"
    path.write_text(json.dumps({"thread_id": "t1", "content": "hello"}, indent=2))

    assert load_rows(path) == [{"thread_id": "t1", "content": "hello"}]


def test_empty_fields_are_preserved_for_dataclass_validation():
    rows = rows_to_fields(
        [{"thread_id": "t1", "role": "", "content": "hello"}],
        frozenset({"thread_id", "role", "content"}),
    )

    assert rows == [{"thread_id": "t1", "role": "", "content": "hello"}]


def test_json_scalar_is_rejected_with_clear_error(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text("42")

    with pytest.raises(ConfigurationError, match="must contain JSON objects"):
        load_rows(path)
