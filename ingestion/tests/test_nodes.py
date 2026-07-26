"""Tests for direct canonical-node ingestion."""

import uuid

import pytest
from zep_cloud.types.add_node_item import AddNodeItem
from zep_cloud.types.add_nodes_response import AddNodesResponse

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.nodes import NodeItem, ingest_nodes

CANONICAL_UUID = "f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47"


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", 123), ("label", 123), ("summary", 123)],
)
def test_non_string_node_fields_raise_configuration_error(field, value):
    kwargs = {"name": "Avery Brown", field: value}
    with pytest.raises(ConfigurationError, match=field):
        NodeItem(**kwargs)


@pytest.mark.parametrize(
    "spelling",
    [
        "F6B6BCBE-6B64-4D3F-9F9E-8F6A6F9F0F47",
        "{f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47}",
        "f6b6bcbe6b644d3f9f9e8f6a6f9f0f47",
    ],
)
def test_node_uuid_spellings_are_canonicalized(spelling):
    node = NodeItem(name="Avery Brown", uuid=spelling)

    # One UUID value, one text: what we dedup on is what we submit.
    assert node.uuid == CANONICAL_UUID
    assert node.to_add_node_item().uuid_ == CANONICAL_UUID


def test_case_different_uuid_spellings_are_rejected_as_duplicates(mock_zep):
    node_uuid = str(uuid.uuid4())
    nodes = [
        NodeItem(name="Avery Brown", uuid=node_uuid),
        NodeItem(name="Avery B.", uuid=node_uuid.upper()),
    ]

    # Both spellings are one node, so the second would silently overwrite the
    # first — the error must name the UUID to be actionable on a large plan.
    with pytest.raises(ConfigurationError, match=node_uuid):
        ingest_nodes(mock_zep, nodes, graph_id="g1")

    mock_zep.graph.add_nodes.assert_not_called()


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


def test_node_submission_without_task_id_is_untracked(mock_zep):
    mock_zep.graph.add_nodes.return_value = AddNodesResponse()
    node = NodeItem(name="Avery Brown", uuid=str(uuid.uuid4()))

    result = ingest_nodes(mock_zep, [node], graph_id="g1")

    assert result.items_submitted == 1
    assert result.untracked_items == 1
    assert result.status == "untracked"


def test_node_wait_polls_until_terminal(mock_zep):
    node = NodeItem(name="Avery Brown", uuid=str(uuid.uuid4()))

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
        yield NodeItem(name="Avery Brown", uuid=str(uuid.uuid4()))
        yield NodeItem(name="Blake Carter", uuid=str(uuid.uuid4()), metadata={1: "sales"})

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
        yield NodeItem(name="Avery Brown", uuid=str(uuid.uuid4()))
        yield NodeItem(name="Blake Carter", uuid=str(uuid.uuid4()), **{field: {"score": value}})

    with pytest.raises(ConfigurationError, match="not valid JSON"):
        ingest_nodes(mock_zep, plan(), graph_id="g1")

    mock_zep.graph.add_nodes.assert_not_called()


def test_non_finite_attribute_from_a_json_file_fails_before_any_api_call(mock_zep, tmp_path):
    # json.loads reads bare NaN / Infinity as a Python extension, so a row file
    # can carry one all the way to the wire, where it is not valid JSON. The
    # failure must name the file's field, not surface as a serialization error.
    path = tmp_path / "nodes.jsonl"
    path.write_text(
        '{"name": "Avery Brown", "uuid": "' + CANONICAL_UUID + '", '
        '"attributes": {"score": NaN, "ratios": [1.0, Infinity]}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as error:
        ingest_nodes(mock_zep, path, graph_id="g1")

    assert "attributes['score']" in str(error.value)
    assert "not valid JSON" in str(error.value)
    mock_zep.graph.add_nodes.assert_not_called()


def test_finite_float_attributes_are_accepted(mock_zep):
    # the guard is finiteness, not magnitude
    node = NodeItem(name="Avery Brown", uuid=CANONICAL_UUID, attributes={"score": 1e308})
    ingest_nodes(mock_zep, [node], graph_id="g1")

    assert mock_zep.graph.add_nodes.call_args.kwargs["nodes"][0].attributes == {"score": 1e308}


def test_empty_node_maps_are_sent_to_clear_existing_values():
    node = NodeItem(
        name="Avery Brown",
        uuid=str(uuid.uuid4()),
        attributes={},
        metadata={},
    )

    item = node.to_add_node_item()

    assert item.attributes == {}
    assert item.metadata == {}
    assert {"attributes", "metadata"} <= item.model_fields_set
