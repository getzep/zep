"""Tests for JsonNormalizer — the docs' JSON-shaping algorithm, automated."""

import json

from zep_ingest.transforms.json_normalizer import JsonNormalizer
from zep_ingest.types import Episode


def apply(normalizer: JsonNormalizer, record: dict, **episode_kwargs) -> list[Episode]:
    ep = Episode(data=json.dumps(record), data_type="json", **episode_kwargs)
    return list(normalizer.apply([ep]))


class TestPassThrough:
    def test_small_flat_record_unchanged(self):
        record = {"id": "P1", "name": "Runners", "description": "shoes", "price": 125}
        [out] = apply(JsonNormalizer(), record)
        assert json.loads(out.data) == record

    def test_non_json_episode_untouched(self):
        normalizer = JsonNormalizer()
        [out] = list(normalizer.apply([Episode(data="plain text " * 500)]))
        assert out.data_type == "text"

    def test_unparseable_json_passes_with_warning(self):
        normalizer = JsonNormalizer()
        [out] = list(normalizer.apply([Episode(data="{broken", data_type="json")]))
        assert out.data == "{broken"
        assert any("json" in w.lower() for w in normalizer.warnings)


class TestWideObjects:
    def test_split_duplicates_identity_fields(self):
        record = {"id": "P1", "name": "Runners", "description": "shoes"}
        record.update({f"prop{i}": i for i in range(12)})
        pieces = apply(JsonNormalizer(max_props=6), record)
        assert len(pieces) == 2
        for piece in pieces:
            parsed = json.loads(piece.data)
            assert parsed["id"] == "P1"
            assert parsed["name"] == "Runners"
            assert parsed["description"] == "shoes"
            non_identity = {k for k in parsed if k not in ("id", "name", "description")}
            assert len(non_identity) <= 6
        merged = {}
        for piece in pieces:
            merged.update(json.loads(piece.data))
        assert all(merged[f"prop{i}"] == i for i in range(12))


class TestLists:
    def test_long_list_exploded_with_contextualizing_field(self):
        record = {"id": "G1", "name": "Garage", "cars": [{"model": "A"}, {"model": "B"}]}
        pieces = apply(JsonNormalizer(max_list_items=1), record)
        car_pieces = [json.loads(p.data) for p in pieces if "model" in json.loads(p.data)]
        assert len(car_pieces) == 2
        for parsed in car_pieces:
            assert parsed["item_type"] == "car"
            assert parsed["id"] == "G1"
        base = [json.loads(p.data) for p in pieces if "model" not in json.loads(p.data)]
        assert base and "cars" not in base[0]

    def test_scalar_list_elements_wrapped(self):
        record = {"id": "T1", "tags": ["red", "blue"]}
        pieces = apply(JsonNormalizer(max_list_items=1), record)
        tag_pieces = [json.loads(p.data) for p in pieces if "value" in json.loads(p.data)]
        assert {p["value"] for p in tag_pieces} == {"red", "blue"}
        assert all(p["item_type"] == "tag" for p in tag_pieces)

    def test_identityless_record_fully_exploded_emits_no_empty_piece(self):
        record = {"tags": ["red", "blue"]}
        pieces = apply(JsonNormalizer(max_list_items=1), record)
        assert [json.loads(p.data) for p in pieces] == [
            {"item_type": "tag", "value": "red"},
            {"item_type": "tag", "value": "blue"},
        ]

    def test_short_list_kept_inline(self):
        record = {"id": "T1", "tags": ["red"]}
        [out] = apply(JsonNormalizer(max_list_items=3), record)
        assert json.loads(out.data)["tags"] == ["red"]


class TestNesting:
    def test_deep_nesting_flattened_with_key_paths(self):
        record = {"id": "N1", "a": {"b": {"c": {"d": {"e": 1}}}}}
        [out] = apply(JsonNormalizer(max_depth=3), record)
        parsed = json.loads(out.data)
        assert parsed["a_b_c_d_e"] == 1
        assert parsed["id"] == "N1"

    def test_shallow_nesting_kept(self):
        record = {"id": "N1", "a": {"b": 1}}
        [out] = apply(JsonNormalizer(max_depth=3), record)
        assert json.loads(out.data)["a"] == {"b": 1}


class TestLongStrings:
    def test_long_string_extracted_as_text_episode(self):
        long_text = "This is a paragraph of prose. " * 100
        record = {"id": "D1", "name": "Report", "body": long_text}
        pieces = apply(JsonNormalizer(long_string_chars=1000), record)
        text_eps = [p for p in pieces if p.data_type == "text"]
        json_eps = [p for p in pieces if p.data_type == "json"]
        assert len(text_eps) == 1
        assert long_text.strip() in text_eps[0].data
        assert "body" in text_eps[0].data  # contextualizing preamble names the field
        assert "Report" in text_eps[0].data
        for piece in json_eps:
            assert long_text not in piece.data


class TestFieldInheritance:
    def test_created_at_and_metadata_inherited(self):
        record = {"id": "P1", "cars": [{"m": 1}, {"m": 2}]}
        pieces = apply(
            JsonNormalizer(max_list_items=1),
            record,
            created_at="2024-06-15T00:00:00Z",
            metadata={"source": "crm"},
        )
        for piece in pieces:
            assert piece.created_at == "2024-06-15T00:00:00Z"
            assert piece.metadata is not None
            assert piece.metadata["source"] == "crm"
