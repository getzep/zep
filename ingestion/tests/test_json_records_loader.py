"""Tests for JsonRecordsLoader (JSONL / CSV / JSON-array → json episodes)."""

import json

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.json_records import JsonRecordsLoader

RECORDS = [
    {
        "sku": "P1",
        "title": "Sample Product A",
        "about": "Comfy shoes",
        "date": "2024-06-15T00:00:00Z",
    },
    {
        "sku": "P2",
        "title": "Sample Product B",
        "about": "Fast shoes",
        "date": "2024-07-01T00:00:00Z",
    },
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
        "P1,Sample Product A,Comfy shoes,2024-06-15T00:00:00Z\n"
        "P2,Sample Product B,Fast shoes,2024-07-01T00:00:00Z\n"
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
        assert json.loads(episodes[1].data)["title"] == "Sample Product B"

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
        assert record["name"] == "Sample Product A"
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

    def test_naive_created_at_is_rejected_instead_of_assumed_utc(self, tmp_path):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": 1, "date": "2024-06-15T10:30:00"}))
        loader = JsonRecordsLoader(file, created_at_field="date")
        [episode] = loader.load()
        assert episode.created_at is None
        assert any("unparseable" in warning for warning in loader.warnings)

    @pytest.mark.parametrize("value", [True, False])
    def test_boolean_created_at_is_rejected_instead_of_treated_as_epoch(self, tmp_path, value):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": 1, "date": value}))
        loader = JsonRecordsLoader(file, created_at_field="date")

        [episode] = loader.load()

        assert episode.created_at is None
        assert any("unparseable" in warning for warning in loader.warnings)

    def test_metadata_fields_lifted(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file, metadata_fields=["sku"]).load())
        assert episodes[0].metadata["sku"] == "P1"

    def test_metadata_carries_source_type_and_filename(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file).load())
        assert episodes[0].metadata["source_type"] == "json_record"
        assert episodes[0].metadata["file_name"] == "products.jsonl"


class TestReservedProvenanceKeys:
    def test_record_fields_cannot_overwrite_provenance(self, tmp_path):
        file = tmp_path / "web.csv"
        file.write_text("source_type,file_name,body\nweb,report-q3.pdf,hello\n")
        loader = JsonRecordsLoader(file, metadata_fields=["source_type", "file_name"])

        [episode] = loader.load()

        assert episode.metadata["source_type"] == "json_record"
        assert episode.metadata["file_name"] == "web.csv"
        # dropped from metadata only; the record itself is untouched
        assert json.loads(episode.data)["source_type"] == "web"
        assert json.loads(episode.data)["file_name"] == "report-q3.pdf"

    def test_dropped_collision_warns(self, tmp_path):
        file = tmp_path / "web.csv"
        file.write_text("source_type,body\nweb,hello\n")
        loader = JsonRecordsLoader(file, metadata_fields=["source_type"])

        list(loader.load())

        assert any("metadata_fields" in warning for warning in loader.warnings)
        assert any("source_type" in warning for warning in loader.warnings)

    def test_collision_warns_even_when_no_record_carries_the_field(self, jsonl_file):
        loader = JsonRecordsLoader(jsonl_file, metadata_fields=["source_type"])

        # the collision is config-level, so preview() and run() report it alike
        list(loader.load())

        assert any("source_type" in warning for warning in loader.warnings)

    def test_other_metadata_fields_still_lift_alongside_a_collision(self, tmp_path):
        file = tmp_path / "web.csv"
        file.write_text("source_type,region,body\nweb,emea,hello\n")
        loader = JsonRecordsLoader(file, metadata_fields=["source_type", "region"])

        [episode] = loader.load()

        assert episode.metadata["region"] == "emea"
        assert episode.metadata["source_type"] == "json_record"


class TestMetadataBudget:
    def test_over_budget_metadata_fields_rejected(self, jsonl_file):
        with pytest.raises(ConfigurationError, match="metadata_fields"):
            JsonRecordsLoader(jsonl_file, metadata_fields=[f"f{i}" for i in range(9)])

    def test_over_budget_rejected_before_any_file_is_read(self, tmp_path):
        # the metadata_fields error wins over "no files match": the request is
        # invalid regardless of what is on disk
        with pytest.raises(ConfigurationError, match="metadata_fields"):
            JsonRecordsLoader(
                str(tmp_path / "*.jsonl"), metadata_fields=[f"f{i}" for i in range(9)]
            )

    def test_full_budget_fits_the_api_metadata_limit(self, tmp_path):
        fields = [f"f{i}" for i in range(8)]
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({f: i for i, f in enumerate(fields)}))

        [episode] = JsonRecordsLoader(file, metadata_fields=fields).load()

        assert len(episode.metadata) == 10
        assert episode.metadata["source_type"] == "json_record"


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
