"""Tests for FactTriple validation and ingest_fact_triples."""

import json

import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.types.add_triple_response import AddTripleResponse

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.triples import FactTriple, ingest_fact_triples


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda _: None)


def triple(**overrides) -> FactTriple:
    kwargs = {
        "fact": "Avery Brown met Blake Carter",
        "fact_name": "MET",
        "source_node_name": "Avery Brown",
        "target_node_name": "Morgan Lee",
    }
    kwargs.update(overrides)
    return FactTriple(**kwargs)


class TestValidation:
    def test_valid_triple_constructs(self):
        triple()

    @pytest.mark.parametrize("field", ["fact", "fact_name", "source_node_name", "target_node_name"])
    def test_required_string_fields_reject_empty_values(self, field):
        with pytest.raises(ConfigurationError, match=field):
            triple(**{field: "   "})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("fact", "x" * 251),
            ("fact", 123),
            ("fact_name", "A" * 51),
            ("fact_name", 123),
            ("fact_name", "lowercase_name"),
            ("fact_name", "HAS SPACE"),
            ("source_node_name", "x" * 51),
            ("target_node_name", "x" * 51),
            ("source_node_summary", "x" * 501),
            ("target_node_summary", "x" * 501),
            ("valid_at", "June 5th 2024"),
            ("valid_at", 20240615),
            ("invalid_at", "not-a-date"),
        ],
    )
    def test_field_limits_raise_naming_the_field(self, field, value):
        with pytest.raises(ConfigurationError, match=field):
            triple(**{field: value})

    def test_nested_attribute_raises(self):
        with pytest.raises(ConfigurationError, match="attributes"):
            triple(attributes={"nested": {"a": 1}})

    def test_scalar_attributes_ok(self):
        triple(attributes={"confidence": "high", "priority": 1, "active": True})

    def test_scalar_array_attributes_ok(self):
        triple(attributes={"tags": ["gtm", "q3"], "reviewer_ids": [1, 2]})

    @pytest.mark.parametrize("value", [[], ["gtm", None], [["gtm"]], [{"tag": "gtm"}]])
    def test_attribute_arrays_the_api_rejects_raise(self, value):
        with pytest.raises(ConfigurationError, match="attributes"):
            triple(attributes={"tags": value})

    @pytest.mark.parametrize(
        "value",
        [float("nan"), float("inf"), float("-inf"), [float("nan")], [1.0, float("inf")]],
    )
    def test_non_finite_attributes_raise(self, value):
        # a bare NaN / Infinity on the wire is not valid JSON
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            triple(attributes={"score": value})

    def test_finite_float_attributes_ok(self):
        # the guard is finiteness, not magnitude
        triple(attributes={"score": 1e308, "ratios": [0.5, 1.5]})

    def test_scalar_array_metadata_ok(self):
        triple(metadata={"teams": ["sales", "support"]})

    @pytest.mark.parametrize("field", ["attributes", "metadata"])
    def test_non_string_map_keys_raise_naming_the_key(self, field):
        with pytest.raises(ConfigurationError, match=f"{field} keys must be strings, got int: 1"):
            triple(**{field: {1: "high"}})

    @pytest.mark.parametrize("field", ["attributes", "metadata"])
    def test_blank_map_keys_raise(self, field):
        with pytest.raises(ConfigurationError, match=f"{field} keys must be non-empty strings"):
            triple(**{field: {"": "high"}})

    def test_string_keys_with_scalar_and_array_values_ok(self):
        # Key validation sits alongside the value rule; neither shape regresses.
        triple(attributes={"tags": ["gtm", "q3"], "confidence": "high"})

    def test_metadata_over_ten_keys_raises(self):
        with pytest.raises(ConfigurationError, match="metadata"):
            triple(metadata={f"k{i}": i for i in range(11)})

    def test_node_labels_accepted_and_mapped(self, mock_zep):
        triple = FactTriple(
            fact="Avery Brown works at Example Organization",
            fact_name="WORKS_AT",
            source_node_name="Avery Brown",
            target_node_name="Example Organization",
            source_node_labels=["Person"],
            target_node_labels=["Organization"],
        )
        ingest_fact_triples(mock_zep, [triple], graph_id="g1")
        kwargs = mock_zep.graph.add_fact_triple.call_args.kwargs
        assert kwargs["source_node_labels"] == ["Person"]
        assert kwargs["target_node_labels"] == ["Organization"]

    def test_more_than_one_label_raises(self):
        with pytest.raises(ConfigurationError, match="labels"):
            FactTriple(
                fact="Avery Brown works at Example Organization",
                fact_name="WORKS_AT",
                source_node_name="Avery Brown",
                target_node_name="Example Organization",
                source_node_labels=["Person", "Employee"],
            )

    def test_rfc3339_timestamps_ok(self):
        triple(valid_at="2024-06-15T10:30:00Z", invalid_at="2024-07-01T00:00:00+00:00")


class TestIngest:
    def test_submits_in_order_with_mapped_kwargs(self, mock_zep):
        triples = [
            triple(fact="A met B", attributes={"confidence": "high"}),
            triple(fact="C met D"),
        ]
        result = ingest_fact_triples(mock_zep, triples, graph_id="g1")
        assert result.method == "sequential"
        assert result.items_submitted == 2
        calls = mock_zep.graph.add_fact_triple.call_args_list
        assert [c.kwargs["fact"] for c in calls] == ["A met B", "C met D"]
        assert calls[0].kwargs["fact_name"] == "MET"
        assert calls[0].kwargs["source_node_name"] == "Avery Brown"
        assert calls[0].kwargs["edge_attributes"] == {"confidence": "high"}
        assert all(c.kwargs["graph_id"] == "g1" for c in calls)
        assert result.task_ids == ["task-1"]
        assert result.status == "queued"

    def test_wait_polls_until_terminal(self, mock_zep):
        result = ingest_fact_triples(mock_zep, [triple()], graph_id="g1")
        result.wait(poll_interval=0)
        assert result.status == "succeeded"
        mock_zep.task.get.assert_called()

    def test_submission_without_task_id_is_untracked(self, mock_zep):
        mock_zep.graph.add_fact_triple.return_value = AddTripleResponse()

        result = ingest_fact_triples(mock_zep, [triple()], graph_id="g1")

        assert result.items_submitted == 1
        assert result.untracked_items == 1
        assert result.status == "untracked"

    def test_destination_required(self, mock_zep):
        with pytest.raises(ConfigurationError):
            ingest_fact_triples(mock_zep, [triple()])

    def test_validation_happens_before_any_call(self, mock_zep, tmp_path):
        file = tmp_path / "triples.jsonl"
        rows = [
            {"fact": "valid", "fact_name": "MET", "source_node_name": "A", "target_node_name": "B"},
            {
                "fact": "x" * 251,
                "fact_name": "MET",
                "source_node_name": "A",
                "target_node_name": "B",
            },
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        with pytest.raises(ConfigurationError):
            ingest_fact_triples(mock_zep, file, graph_id="g1")
        mock_zep.graph.add_fact_triple.assert_not_called()

    def test_429_retried(self, mock_zep):
        mock_zep.graph.add_fact_triple.side_effect = [
            ApiError(status_code=429, headers={"Retry-After": "1"}),
            None,
        ]
        result = ingest_fact_triples(mock_zep, [triple()], graph_id="g1")
        assert result.add_errors == []
        assert result.items_submitted == 1

    def test_failure_recorded_and_continues(self, mock_zep):
        mock_zep.graph.add_fact_triple.side_effect = [
            ApiError(status_code=400, body="bad"),
            None,
        ]
        result = ingest_fact_triples(mock_zep, [triple(), triple()], graph_id="g1")
        assert len(result.add_errors) == 1
        assert result.add_errors[0].index == 0
        assert result.items_submitted == 1

    def test_jsonl_file_source(self, mock_zep, tmp_path):
        file = tmp_path / "triples.jsonl"
        rows = [
            {
                "fact": "Avery Brown met Blake Carter",
                "fact_name": "MET",
                "source_node_name": "Avery Brown",
                "target_node_name": "Blake Carter",
            },
            {
                "fact": "Ana owns GTM",
                "fact_name": "RESPONSIBLE",
                "source_node_name": "Ana",
                "target_node_name": "GTM analytics",
            },
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        result = ingest_fact_triples(mock_zep, file, user_id="u1")
        assert result.items_submitted == 2
        assert mock_zep.graph.add_fact_triple.call_args.kwargs["fact_name"] == "RESPONSIBLE"

    def test_json_array_file_source(self, mock_zep, tmp_path):
        file = tmp_path / "triples.json"
        rows = [
            {
                "fact": "Avery Brown met Blake Carter",
                "fact_name": "MET",
                "source_node_name": "Avery Brown",
                "target_node_name": "Blake Carter",
            },
            {
                "fact": "Ana owns GTM",
                "fact_name": "RESPONSIBLE",
                "source_node_name": "Ana",
                "target_node_name": "GTM analytics",
            },
        ]
        file.write_text(json.dumps(rows, indent=2))
        result = ingest_fact_triples(mock_zep, file, graph_id="g1")
        assert result.items_submitted == 2
        assert mock_zep.graph.add_fact_triple.call_args.kwargs["fact_name"] == "RESPONSIBLE"

    def test_csv_file_source_rejected_with_clear_error(self, mock_zep, tmp_path):
        # CSV cannot express the list/mapping fields (labels, attributes,
        # metadata) these schemas rely on, so it is rejected at the file layer
        # with a message pointing to JSONL/JSON (and to ingest_json_records).
        file = tmp_path / "triples.csv"
        file.write_text(
            "fact,fact_name,source_node_name,target_node_name\n"
            "Avery Brown met Blake Carter,MET,Avery Brown,Blake Carter\n"
        )
        with pytest.raises(ConfigurationError, match="CSV"):
            ingest_fact_triples(mock_zep, file, graph_id="g1")
        mock_zep.graph.add_fact_triple.assert_not_called()

    def test_file_with_invalid_row_names_the_row(self, mock_zep, tmp_path):
        file = tmp_path / "triples.jsonl"
        file.write_text(
            json.dumps(
                {
                    "fact": "x" * 251,
                    "fact_name": "MET",
                    "source_node_name": "A",
                    "target_node_name": "B",
                }
            )
        )
        with pytest.raises(ConfigurationError, match="fact"):
            ingest_fact_triples(mock_zep, file, graph_id="g1")

    def test_row_missing_required_field_names_the_field_and_row(self, mock_zep, tmp_path):
        # an omitted column is a ConfigurationError pointing at the row, not the
        # dataclass constructor's bare TypeError
        file = tmp_path / "triples.jsonl"
        rows = [
            {
                "fact": "Avery Brown met Blake Carter",
                "fact_name": "MET",
                "source_node_name": "Avery Brown",
                "target_node_name": "Blake Carter",
            },
            {"fact": "Ana owns GTM", "fact_name": "RESPONSIBLE", "source_node_name": "Ana"},
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        with pytest.raises(
            ConfigurationError, match=r"Row 1 is missing required field\(s\): target_node_name"
        ):
            ingest_fact_triples(mock_zep, file, graph_id="g1")
        mock_zep.graph.add_fact_triple.assert_not_called()


class TestNodeUuidPinning:
    def test_valid_uuids_accepted_and_mapped(self):
        import uuid as uuid_module

        source, target = str(uuid_module.uuid4()), str(uuid_module.uuid4())
        t = triple(source_node_uuid=source, target_node_uuid=target)
        kwargs = t.to_api_kwargs(
            __import__("zep_ingest.types", fromlist=["Destination"]).Destination(graph_id="g")
        )
        assert kwargs["source_node_uuid"] == source
        assert kwargs["target_node_uuid"] == target
        assert "fact_uuid" not in kwargs

    def test_invalid_uuid_raises_naming_the_field(self):
        with pytest.raises(ConfigurationError, match="source_node_uuid"):
            triple(source_node_uuid="nope")

    def test_client_supplied_fact_uuid_field_is_rejected(self):
        with pytest.raises(TypeError, match="fact_uuid"):
            triple(fact_uuid="f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47")  # type: ignore[call-arg]

    def test_json_row_with_fact_uuid_is_rejected_before_any_api_call(self, mock_zep, tmp_path):
        path = tmp_path / "triples.jsonl"
        path.write_text(
            json.dumps(
                {
                    "fact": "Avery Brown met Blake Carter",
                    "fact_name": "MET",
                    "source_node_name": "Avery Brown",
                    "target_node_name": "Blake Carter",
                    "fact_uuid": "f6b6bcbe-6b64-4d3f-9f9e-8f6a6f9f0f47",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="fact_uuid cannot be supplied"):
            ingest_fact_triples(mock_zep, path, graph_id="g1")

        mock_zep.graph.add_fact_triple.assert_not_called()
