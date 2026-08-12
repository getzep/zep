"""Tests for direct canonical-node ingestion."""

import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.types.add_node_item import AddNodeItem
from zep_cloud.types.add_nodes_response import AddNodesResponse
from zep_cloud.types.added_node import AddedNode

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


def test_node_task_id_is_tracked_and_assigned_uuids_are_recorded(mock_zep):
    mock_zep.graph.add_nodes.return_value = AddNodesResponse(
        task_id="node-task-1",
        nodes=[AddedNode(name="Avery Brown", uuid_="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    node = NodeItem(name="Avery Brown")

    result = ingest_nodes(mock_zep, [node], graph_id="g1")

    assert result.task_ids == ["node-task-1"]
    assert result.node_uuids == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
    assert result.batch_ids == []
    assert result.status == "queued"
    # Submitted through the typed SDK method — not a raw transport.
    _, kwargs = mock_zep.graph.add_nodes.call_args
    assert kwargs["graph_id"] == "g1"
    (item,) = kwargs["nodes"]
    assert isinstance(item, AddNodeItem)
    assert item.name == "Avery Brown"
    assert "uuid_" not in item.model_fields_set


def test_node_uuids_preserve_batch_submission_order(mock_zep):
    mock_zep.graph.add_nodes.side_effect = [
        AddNodesResponse(
            task_id="task-a",
            nodes=[
                AddedNode(name="Avery Brown", uuid_="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                AddedNode(name="Blake Carter", uuid_="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            ],
        ),
        AddNodesResponse(
            task_id="task-b",
            nodes=[AddedNode(name="Casey Diaz", uuid_="cccccccc-cccc-4ccc-8ccc-cccccccccccc")],
        ),
    ]
    nodes = [
        NodeItem(name="Avery Brown"),
        NodeItem(name="Blake Carter"),
        NodeItem(name="Casey Diaz"),
    ]

    result = ingest_nodes(mock_zep, nodes, graph_id="g1", batch_size=2)

    assert result.node_uuids == [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ]
    assert result.task_ids == ["task-a", "task-b"]
    assert result.items_submitted == 3


def test_node_uuids_keep_none_gaps_when_earlier_batch_fails(mock_zep):
    mock_zep.graph.add_nodes.side_effect = [
        ApiError(status_code=500, body="boom"),
        AddNodesResponse(
            task_id="task-b",
            nodes=[
                AddedNode(name="Casey Diaz", uuid_="cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                AddedNode(name="Drew Ellis", uuid_="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            ],
        ),
    ]
    nodes = [
        NodeItem(name="Avery Brown"),
        NodeItem(name="Blake Carter"),
        NodeItem(name="Casey Diaz"),
        NodeItem(name="Drew Ellis"),
    ]

    result = ingest_nodes(mock_zep, nodes, graph_id="g1", batch_size=2)

    assert result.node_uuids == [
        None,
        None,
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    ]
    assert [node.name for node, _ in zip(nodes, result.node_uuids, strict=True)] == [
        "Avery Brown",
        "Blake Carter",
        "Casey Diaz",
        "Drew Ellis",
    ]
    assert result.items_submitted == 2
    assert len(result.add_errors) == 1
    assert result.add_errors[0].index == 0
    assert result.task_ids == ["task-b"]


def test_node_uuids_pad_none_when_response_omits_an_entry(mock_zep):
    mock_zep.graph.add_nodes.return_value = AddNodesResponse(
        task_id="task-a",
        nodes=[AddedNode(name="Avery Brown", uuid_="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    nodes = [NodeItem(name="Avery Brown"), NodeItem(name="Blake Carter")]

    result = ingest_nodes(mock_zep, nodes, graph_id="g1")

    assert result.node_uuids == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", None]
    assert result.items_submitted == 2


def test_client_supplied_uuid_field_is_rejected():
    with pytest.raises(TypeError, match="uuid"):
        NodeItem(name="Avery Brown", uuid="f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47")  # type: ignore[call-arg]


def test_json_row_with_uuid_is_rejected_before_any_api_call(mock_zep, tmp_path):
    path = tmp_path / "nodes.jsonl"
    path.write_text(
        '{"name": "Avery Brown", "uuid": "f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="uuid cannot be supplied"):
        ingest_nodes(mock_zep, path, graph_id="g1")

    mock_zep.graph.add_nodes.assert_not_called()


def test_node_submission_without_task_id_is_untracked(mock_zep):
    mock_zep.graph.add_nodes.return_value = AddNodesResponse(
        nodes=[AddedNode(name="Avery Brown", uuid_="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")]
    )
    node = NodeItem(name="Avery Brown")

    result = ingest_nodes(mock_zep, [node], graph_id="g1")

    assert result.items_submitted == 1
    assert result.untracked_items == 1
    assert result.node_uuids == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
    assert result.status == "untracked"


def test_node_wait_polls_until_terminal(mock_zep):
    node = NodeItem(name="Avery Brown")

    result = ingest_nodes(mock_zep, [node], graph_id="g1")
    result.wait(poll_interval=0)

    assert result.status == "succeeded"
    mock_zep.task.get.assert_called()


def test_scalar_arrays_accepted_in_both_node_maps():
    # attributes and metadata answer to the same API rule, so an array of
    # scalars is a valid value in either
    node = NodeItem(
        name="Avery Brown",
        attributes={"aliases": ["Avery B.", "A. Brown"]},
        metadata={"teams": ["sales", "support"]},
    )

    item = node.to_add_node_item()

    assert item.attributes == {"aliases": ["Avery B.", "A. Brown"]}
    assert item.metadata == {"teams": ["sales", "support"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attributes", []),
        ("attributes", ["Avery B.", None]),
        ("attributes", [["Avery B."]]),
        ("attributes", [{"alias": "Avery B."}]),
        ("attributes", {"alias": "Avery B."}),
        ("metadata", []),
        ("metadata", ["sales", None]),
        ("metadata", {"team": "sales"}),
    ],
)
def test_non_scalar_node_map_values_raise_naming_the_field(field, value):
    with pytest.raises(ConfigurationError, match=field):
        NodeItem(name="Avery Brown", **{field: {"aliases": value}})


@pytest.mark.parametrize("field", ["attributes", "metadata"])
@pytest.mark.parametrize("key", [1, True, None])
def test_non_string_node_map_keys_raise_naming_the_key(field, key):
    with pytest.raises(ConfigurationError, match=f"{field} keys must be strings"):
        NodeItem(name="Avery Brown", **{field: {key: "Avery B."}})


@pytest.mark.parametrize("field", ["attributes", "metadata"])
def test_blank_node_map_keys_raise(field):
    with pytest.raises(ConfigurationError, match=f"{field} keys must be non-empty strings"):
        NodeItem(name="Avery Brown", **{field: {"  ": "Avery B."}})


def test_non_string_map_key_fails_before_any_api_call(mock_zep):
    def plan():
        yield NodeItem(name="Avery Brown")
        yield NodeItem(name="Blake Carter", metadata={1: "sales"})

    # The API takes JSON object keys, which are strings. A non-string one fails
    # while the plan is still being materialized — nothing reaches the wire, so
    # there is no half-submitted run to reconcile.
    with pytest.raises(ConfigurationError, match="metadata keys must be strings, got int: 1"):
        ingest_nodes(mock_zep, plan(), graph_id="g1")

    mock_zep.graph.add_nodes.assert_not_called()


@pytest.mark.parametrize("field", ["attributes", "metadata"])
@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), [float("nan")], [1.0, float("inf")]],
)
def test_non_finite_map_value_fails_before_any_api_call(mock_zep, field, value):
    def plan():
        yield NodeItem(name="Avery Brown")
        yield NodeItem(name="Blake Carter", **{field: {"score": value}})

    with pytest.raises(ConfigurationError, match="not valid JSON"):
        ingest_nodes(mock_zep, plan(), graph_id="g1")

    mock_zep.graph.add_nodes.assert_not_called()


def test_non_finite_attribute_from_a_json_file_fails_before_any_api_call(mock_zep, tmp_path):
    # json.loads reads bare NaN / Infinity as a Python extension, so a row file
    # can carry one all the way to the wire, where it is not valid JSON. The
    # failure must name the file's field, not surface as a serialization error.
    path = tmp_path / "nodes.jsonl"
    path.write_text(
        '{"name": "Avery Brown", "attributes": {"score": NaN, "ratios": [1.0, Infinity]}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as error:
        ingest_nodes(mock_zep, path, graph_id="g1")

    assert "attributes['score']" in str(error.value)
    assert "not valid JSON" in str(error.value)
    mock_zep.graph.add_nodes.assert_not_called()


def test_finite_float_attributes_are_accepted(mock_zep):
    # the guard is finiteness, not magnitude
    node = NodeItem(name="Avery Brown", attributes={"score": 1e308})
    ingest_nodes(mock_zep, [node], graph_id="g1")

    assert mock_zep.graph.add_nodes.call_args.kwargs["nodes"][0].attributes == {"score": 1e308}


def test_empty_node_maps_are_sent_to_clear_existing_values():
    node = NodeItem(
        name="Avery Brown",
        attributes={},
        metadata={},
    )

    item = node.to_add_node_item()

    assert item.attributes == {}
    assert item.metadata == {}
    assert {"attributes", "metadata"} <= item.model_fields_set
