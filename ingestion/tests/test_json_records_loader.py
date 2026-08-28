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

    def test_json_suffix_holding_jsonl_falls_back(self, tmp_path):
        """JSONL exports are routinely saved as .json. The shared row reader used by
        the fact-triple and thread paths already accepts that shape."""
        path = tmp_path / "records.json"
        path.write_text('{"sku": "P1"}\n{"sku": "P2"}\n')

        episodes = list(JsonRecordsLoader(path).load())

        assert [json.loads(e.data)["sku"] for e in episodes] == ["P1", "P2"]

    def test_explicit_json_format_does_not_fall_back(self, tmp_path):
        # format="json" is the caller stating the shape, so a decode failure is real
        path = tmp_path / "records.json"
        path.write_text('{"sku": "P1"}\n{"sku": "P2"}\n')

        with pytest.raises(ConfigurationError, match="Unparseable records"):
            list(JsonRecordsLoader(path, format="json").load())

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

    def test_sequence_of_files_keeps_caller_order(self, tmp_path):
        later = tmp_path / "b.jsonl"
        earlier = tmp_path / "a.jsonl"
        later.write_text(json.dumps({"id": "prs"}))
        earlier.write_text(json.dumps({"id": "issues"}))

        episodes = list(JsonRecordsLoader([later, earlier]).load())

        assert [json.loads(episode.data)["id"] for episode in episodes] == ["prs", "issues"]

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

    def test_array_field_lifts_into_metadata(self, tmp_path):
        # the API takes an array of scalars as a metadata value, so a record's
        # tags belong in metadata and not in the episode body alone
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"sku": "P1", "tags": ["shoes", "sale"]}))

        [episode] = JsonRecordsLoader(file, metadata_fields=["tags"]).load()

        assert episode.metadata["tags"] == ["shoes", "sale"]

    def test_fields_the_api_would_reject_are_not_lifted(self, tmp_path):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"dims": {"w": 3}, "tags": [], "sizes": [8, None]}))

        [episode] = JsonRecordsLoader(file, metadata_fields=["dims", "tags", "sizes"]).load()

        assert set(episode.metadata) == {"source_type", "file_name"}
        # dropped from metadata only; the record itself is untouched
        assert json.loads(episode.data)["dims"] == {"w": 3}

    def test_metadata_carries_source_type_and_filename(self, jsonl_file):
        episodes = list(JsonRecordsLoader(jsonl_file).load())
        assert episodes[0].metadata["source_type"] == "json_record"
        assert episodes[0].metadata["file_name"] == "products.jsonl"


class TestMappingSources:
    def test_mapping_target_never_feeds_another_mapping(self, tmp_path):
        # name_field='id' means "use the id column as the display name"; the
        # id_field mapping must not hand it the sku instead
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": "legacy-7", "sku": "P1", "title": "Shoes"}))

        [episode] = JsonRecordsLoader(file, id_field="sku", name_field="id").load()

        record = json.loads(episode.data)
        assert record["id"] == "P1"
        assert record["name"] == "legacy-7"

    def test_mapping_order_does_not_change_the_outcome(self, tmp_path):
        # id and name swap places, which only resolves if each mapping reads the
        # original record — whichever of the two the loader applies first
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": "legacy-7", "name": "Shoes"}))

        [episode] = JsonRecordsLoader(file, id_field="name", name_field="id").load()

        record = json.loads(episode.data)
        assert record["id"] == "Shoes"
        assert record["name"] == "legacy-7"

    def test_chained_mappings_each_read_their_own_source(self, tmp_path):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": "legacy-7", "name": "Shoes", "sku": "P1"}))

        [episode] = JsonRecordsLoader(
            file, id_field="sku", name_field="id", description_field="name"
        ).load()

        record = json.loads(episode.data)
        assert record["id"] == "P1"
        assert record["name"] == "legacy-7"
        assert record["description"] == "Shoes"

    def test_created_at_field_reads_the_record_not_a_mapped_key(self, tmp_path):
        # created_at_field names a column of the caller's record, exactly like the
        # identity mappings do, so mapping something onto 'id' cannot steal it
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": "2024-06-15T00:00:00Z", "sku": "P1"}))
        loader = JsonRecordsLoader(file, id_field="sku", created_at_field="id")

        [episode] = loader.load()

        assert episode.created_at is not None
        assert episode.created_at.startswith("2024-06-15")
        assert json.loads(episode.data)["id"] == "P1"
        assert loader.warnings == []

    def test_metadata_lifts_the_episode_as_emitted(self, tmp_path):
        # the other side of the same rule: metadata_fields names keys of the
        # episode body, so a lifted value always agrees with the body
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"id": "legacy-7", "sku": "P1"}))

        [episode] = JsonRecordsLoader(
            file, id_field="sku", record_type="product", metadata_fields=["id", "record_type"]
        ).load()

        assert episode.metadata["id"] == "P1"
        assert episode.metadata["record_type"] == "product"
        assert json.loads(episode.data)["id"] == "P1"


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


class TestNonFiniteNumbers:
    # json reads NaN/Infinity/-Infinity as a Python extension and writes them
    # back out, so an accepted record would emit an episode body no strict JSON
    # parser reads; the loader refuses the record instead

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_value_rejected_naming_file_field_and_record(self, tmp_path, token):
        file = tmp_path / "prices.jsonl"
        file.write_text('{"sku": "P1", "price": 1.5}\n{"sku": "P2", "price": ' + token + "}\n")

        with pytest.raises(ConfigurationError) as error:
            list(JsonRecordsLoader(file).load())

        message = str(error.value)
        assert "prices.jsonl" in message
        assert "'price'" in message
        assert "record 2" in message

    def test_non_finite_rejected_in_a_json_array(self, tmp_path):
        file = tmp_path / "products.json"
        file.write_text('[{"sku": "P1"}, {"sku": "P2", "qty": Infinity}]')

        with pytest.raises(ConfigurationError) as error:
            list(JsonRecordsLoader(file).load())

        message = str(error.value)
        assert "products.json" in message
        assert "'qty'" in message
        assert "record 2" in message

    def test_nested_non_finite_is_caught(self, tmp_path):
        # the value can sit at any depth; the path locates it inside the record
        file = tmp_path / "r.jsonl"
        file.write_text('{"sku": "P1", "dims": {"sizes": [8, NaN]}}')

        with pytest.raises(ConfigurationError) as error:
            list(JsonRecordsLoader(file).load())

        assert "'dims.sizes[1]'" in str(error.value)

    def test_record_that_is_itself_non_finite_is_rejected(self, tmp_path):
        file = tmp_path / "r.json"
        file.write_text("NaN")

        with pytest.raises(ConfigurationError) as error:
            list(JsonRecordsLoader(file).load())

        assert "r.json" in str(error.value)

    def test_non_finite_metadata_field_rejected_instead_of_nulled(self, tmp_path):
        # the API records a non-finite metadata value as null, silently changing
        # it, so the record is refused rather than the field lifted or skipped
        file = tmp_path / "scores.jsonl"
        file.write_text('{"sku": "P1", "score": NaN}')

        with pytest.raises(ConfigurationError) as error:
            list(JsonRecordsLoader(file, metadata_fields=["score"]).load())

        message = str(error.value)
        assert "scores.jsonl" in message
        assert "'score'" in message

    def test_finite_floats_load_end_to_end(self, tmp_path):
        file = tmp_path / "r.jsonl"
        file.write_text(json.dumps({"sku": "P1", "price": 19.99, "sizes": [8.5, 9.0]}))

        [episode] = JsonRecordsLoader(file, metadata_fields=["price", "sizes"]).load()

        assert json.loads(episode.data)["price"] == 19.99
        assert episode.metadata["price"] == 19.99
        assert episode.metadata["sizes"] == [8.5, 9.0]

    def test_csv_nan_text_stays_a_string(self, tmp_path):
        # csv.DictReader yields strings, so a 'NaN' cell is text and not a float
        file = tmp_path / "products.csv"
        file.write_text("sku,price\nP1,NaN\n")

        [episode] = JsonRecordsLoader(file, metadata_fields=["price"]).load()

        assert json.loads(episode.data)["price"] == "NaN"
        assert episode.metadata["price"] == "NaN"


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
