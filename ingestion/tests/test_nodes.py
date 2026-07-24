"""Tests for direct canonical-node ingestion."""

import uuid

import pytest
from zep_cloud.types.add_node_item import AddNodeItem
from zep_cloud.types.add_nodes_response import AddNodesResponse

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


def test_node_task_id_is_tracked_as_task(mock_zep):
    mock_zep.graph.add_nodes.return_value = AddNodesResponse(task_id="node-task-1")
    node_uuid = str(uuid.uuid4())
    node = NodeItem(name="Avery Brown", uuid=node_uuid)

    result = ingest_nodes(mock_zep, [node], graph_id="g1")

    assert result.task_ids == ["node-task-1"]
    assert result.batch_ids == []
    assert result.status == "queued"
    # Submitted through the typed SDK method — not a raw transport.
    _, kwargs = mock_zep.graph.add_nodes.call_args
    assert kwargs["graph_id"] == "g1"
    (item,) = kwargs["nodes"]
    assert isinstance(item, AddNodeItem)
    # Must populate the SDK's uuid_ field (the client serializes it to the wire
    # "uuid" key); passing the "uuid" alias instead would leave identity unset.
    assert item.uuid_ == node_uuid
