"""Tests for JsonRecordsLoader (JSONL / CSV / JSON-array → json episodes)."""

import json

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.json_records import JsonRecordsLoader

RECORDS = [
    {"sku": "P1", "title": "Wool Runners", "about": "Comfy shoes", "date": "2024-06-15"},
    {"sku": "P2", "title": "Tree Dashers", "about": "Fast shoes", "date": "2024-07-01"},
]


@pytest.fixture
def jsonl_file(tmp_path):
    file = tmp_path / "products.jsonl"
    file.write_text("\n".join(json.dumps(r) for r in RECORDS))
    return file


@pytest.fixture
def csv_file(tmp_path):
    file = tmp_path / "products.csv"
    file.write_text(
        "sku,title,about,date\n"
        "P1,Wool Runners,Comfy shoes,2024-06-15\n"
        "P2,Tree Dashers,Fast shoes,2024-07-01\n"
    )
    return file


@pytest.fixture
def json_array_file(tmp_path):
    file = tmp_path / "products.json"
    file.write_text(json.dumps(RECORDS))
    return file


class TestFormats:
    def test_jsonl(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file).load())
        assert len(episodes) == 2
        assert all(e.data_type == "json" for e in episodes)
        assert json.loads(episodes[0].data)["sku"] == "P1"

    def test_csv(self, csv_file):
        episodes = list(JsonRecordsLoader(csv_file).load())
        assert len(episodes) == 2
        assert json.loads(episodes[1].data)["title"] == "Tree Dashers"

    def test_json_array(self, json_array_file):
        episodes = list(JsonRecordsLoader(json_array_file).load())
        assert len(episodes) == 2

    def test_glob_over_multiple_files(self, tmp_path):
        for i in range(2):
            (tmp_path / f"part{i}.jsonl").write_text(json.dumps({"id": i}))
        episodes = list(JsonRecordsLoader(str(tmp_path / "*.jsonl")).load())
        assert len(episodes) == 2

    def test_no_match_raises(self, tmp_path):
        with pytest.raises(ConfigurationError):
            JsonRecordsLoader(str(tmp_path / "*.jsonl"))


class TestFieldMapping:
    def test_identity_fields_mapped(self, jsonl_file):
        episodes = list(
            JsonRecordsLoader(
                jsonl_file, id_field="sku", name_field="title", description_field="about"
            ).load()
        )
        record = json.loads(episodes[0].data)
        assert record["id"] == "P1"
        assert record["name"] == "Wool Runners"
        assert record["description"] == "Comfy shoes"

    def test_record_type_injected(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file, record_type="product").load())
        assert json.loads(episodes[0].data)["record_type"] == "product"

    def test_created_at_field_parsed_to_rfc3339(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file, created_at_field="date").load())
        assert episodes[0].created_at is not None
        assert episodes[0].created_at.startswith("2024-06-15")

    def test_missing_created_at_field_warns(self, tmp_path):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": 1}))
        loader = JsonRecordsLoader(file, created_at_field="date")
        episodes = list(loader.load())
        assert episodes[0].created_at is None
        assert any("date" in w for w in loader.warnings)

    def test_metadata_fields_lifted(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file, metadata_fields=["sku"]).load())
        assert episodes[0].metadata["sku"] == "P1"

    def test_source_description_is_filename(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file).load())
        assert episodes[0].source_description == "products.jsonl"


class TestOneLiner:
    def test_ingest_json_records(self, mock_zep, jsonl_file):
        from zep_ingest.pipeline import ingest_json_records

        result = ingest_json_records(
            mock_zep,
            jsonl_file,
            graph_id="catalog",
            id_field="sku",
            created_at_field="date",
        )
        assert result.items_submitted == 2
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert all(i.data_type == "json" for i in items)
        assert all(i.created_at is not None for i in items)
        assert json.loads(items[0].data)["id"] == "P1"
