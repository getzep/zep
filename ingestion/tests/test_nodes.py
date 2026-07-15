"""Tests for direct canonical-node ingestion."""

import uuid

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.nodes import NodeItem, ingest_nodes


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", 123), ("label", 123), ("summary", 123)],
)
def test_non_string_node_fields_raise_configuration_error(field, value):
    kwargs = {"name": "Avery Brown", field: value}
    with pytest.raises(ConfigurationError, match=field):
        NodeItem(**kwargs)


def test_node_task_id_is_tracked_as_task(mock_zep, monkeypatch):
    monkeypatch.setattr(
        "zep_ingest.nodes._post_batch", lambda client, payload: {"task_id": "node-task-1"}
    )
    node = NodeItem(name="Avery Brown", uuid=str(uuid.uuid4()))

    result = ingest_nodes(mock_zep, [node], graph_id="g1")

    assert result.task_ids == ["node-task-1"]
    assert result.batch_ids == []
    assert result.status == "queued"
