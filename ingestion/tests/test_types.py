"""Tests for core data model: Episode, Destination, and API mappings."""

import json

import pytest
from zep_cloud.types.batch_add_item import BatchAddItem

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import (
    DEFAULT_ITEMS_PER_BATCH,
    MAX_EPISODE_CHARS,
    MAX_ITEMS_PER_ADD,
    MAX_ITEMS_PER_BATCH,
    MAX_METADATA_KEYS,
    SAFE_EPISODE_CHARS,
    Destination,
    Episode,
    to_batch_item,
    to_graph_add_kwargs,
)


class TestConstants:
    def test_limits_match_documented_api_constraints(self):
        assert MAX_EPISODE_CHARS == 10_000
        assert SAFE_EPISODE_CHARS == 9_500
        assert MAX_ITEMS_PER_ADD == 350
        assert DEFAULT_ITEMS_PER_BATCH == 10_000
        assert MAX_ITEMS_PER_BATCH == 50_000
        assert DEFAULT_ITEMS_PER_BATCH < MAX_ITEMS_PER_BATCH
        assert MAX_METADATA_KEYS == 10


class TestEpisode:
    def test_defaults(self):
        ep = Episode(data="hello")
        assert ep.data == "hello"
        assert ep.data_type == "text"
        assert ep.created_at is None
        assert ep.metadata is None
        assert ep.document is None

    def test_document_excluded_from_repr(self):
        ep = Episode(data="chunk", document="a" * 100)
        assert "document" not in repr(ep)


class TestMetadataValues:
    """The API takes a scalar or an array of scalars for every metadata and
    attribute value, so the client refuses exactly what it would refuse."""

    @pytest.mark.parametrize(
        "value",
        ["sales", 7, 1.5, True, None, ["sales", "support"], [1, 2], ("sales", "support")],
    )
    def test_scalars_and_scalar_arrays_accepted(self, value):
        assert Episode(data="x", metadata={"teams": value}).metadata == {"teams": value}

    @pytest.mark.parametrize(
        "value",
        [
            [],  # an empty array carries no meaning
            ["sales", None],  # a null element is not a value
            [["sales"]],
            [{"name": "sales"}],
            {"name": "sales"},
            {"sales", "support"},  # a set has no JSON form to send
        ],
    )
    def test_non_scalar_values_rejected(self, value):
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata={"teams": value})

    def test_rejection_names_the_key_and_the_accepted_shapes(self):
        with pytest.raises(ConfigurationError) as error:
            Episode(data="x", metadata={"teams": {"name": "sales"}})

        assert "metadata['teams']" in str(error.value)
        assert "arrays of scalars" in str(error.value)

    @pytest.mark.parametrize(
        "value",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            [float("nan")],  # refused inside an array as well as alone
            [1.0, float("inf")],  # one bad element condemns the array
        ],
    )
    def test_non_finite_numbers_rejected(self, value):
        # json accepts NaN/Infinity on the way in and writes them straight back
        # out, so an accepted one reaches the API as a bare NaN / Infinity token
        # that no strict JSON parser accepts.
        with pytest.raises(ConfigurationError, match="not valid JSON"):
            Episode(data="x", metadata={"score": value})

    def test_non_finite_rejection_names_the_key_and_the_value(self):
        with pytest.raises(ConfigurationError) as error:
            Episode(data="x", metadata={"score": float("inf")})

        assert "metadata['score']" in str(error.value)
        # "is inf;" rather than "inf", which -inf would satisfy too — the value is
        # quoted back to the caller, so a wrong-sign report has to fail here
        assert "is inf;" in str(error.value)

    def test_nested_array_of_non_finites_reports_the_shape_not_the_value(self):
        # [[nan]] is refused for its nesting, not its NaN: _first_non_finite only
        # looks one level down, so the non-finite branch must not shadow the
        # generic shape error for a value it cannot actually see
        with pytest.raises(ConfigurationError) as error:
            Episode(data="x", metadata={"score": [[float("nan")]]})

        assert "arrays of scalars" in str(error.value)
        assert "not valid JSON" not in str(error.value)

    def test_non_finite_beside_a_non_scalar_reports_only_the_non_finite(self):
        # the two branches are exclusive: a value that is both non-finite and
        # otherwise invalid is named once, by the more specific message
        with pytest.raises(ConfigurationError) as error:
            Episode(data="x", metadata={"score": [float("nan"), {"a": 1}]})

        assert "is nan;" in str(error.value)
        assert "arrays of scalars" not in str(error.value)

    def test_largest_finite_float_still_accepted(self):
        # the guard is finiteness, not magnitude
        assert Episode(data="x", metadata={"score": 1e308}).metadata == {"score": 1e308}

    def test_int_too_large_for_a_float_accepted_here_unlike_in_timing_config(self):
        # JSON integers are arbitrary-precision, so this serializes and reparses
        # exactly: nothing about it is unsendable. require_nonnegative_number
        # refuses the same value, because there the limit is what a C clock can
        # hold, not what JSON can express. Do not "unify" the two guards.
        huge = 10**400
        assert Episode(data="x", metadata={"score": huge}).metadata == {"score": huge}
        assert json.loads(json.dumps({"score": huge}))["score"] == huge

    def test_non_mapping_metadata_rejected(self):
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata=["sales"])


class TestMetadataKeys:
    """A JSON object key is a string, so a non-string key is a named error here
    rather than a serialization failure once a request is already in flight."""

    @pytest.mark.parametrize("key", [1, 1.5, True, None, ("teams",)])
    def test_non_string_keys_rejected(self, key):
        with pytest.raises(ConfigurationError, match="metadata keys must be strings"):
            Episode(data="x", metadata={key: "sales"})

    def test_rejection_names_the_offending_key(self):
        with pytest.raises(ConfigurationError) as error:
            Episode(data="x", metadata={"teams": "sales", 7: "support"})

        # The key and its type, so it is findable in a large mapping.
        assert "metadata keys must be strings, got int: 7" in str(error.value)

    @pytest.mark.parametrize("key", ["", "   "])
    def test_blank_keys_rejected(self, key):
        # Consistent with every required string in this package, which counts
        # whitespace-only as empty.
        with pytest.raises(ConfigurationError, match="metadata keys must be non-empty strings"):
            Episode(data="x", metadata={key: "sales"})

    @pytest.mark.parametrize("key", ["teams", "team names", "équipes", "k" * 200])
    def test_any_other_string_key_accepted(self, key):
        # The API limits how many keys a map has, not how they are spelled; a
        # length or charset rule invented here would refuse data it accepts.
        assert Episode(data="x", metadata={key: ["sales", "support"]}).metadata == {
            key: ["sales", "support"]
        }

    def test_keys_are_neither_trimmed_nor_coerced(self):
        # Rewriting a key would rename a field the caller has to filter on later,
        # so a usable key is stored exactly as written.
        assert Episode(data="x", metadata={" teams ": "sales"}).metadata == {" teams ": "sales"}


class TestDestination:
    def test_graph_id_only_is_valid(self):
        dest = Destination(graph_id="g1")
        assert dest.graph_id == "g1"
        assert dest.user_id is None

    def test_user_id_only_is_valid(self):
        dest = Destination(user_id="u1")
        assert dest.user_id == "u1"

    def test_both_raises(self):
        with pytest.raises(ConfigurationError):
            Destination(graph_id="g1", user_id="u1")

    def test_neither_raises(self):
        with pytest.raises(ConfigurationError):
            Destination()

    def test_frozen(self):
        dest = Destination(graph_id="g1")
        with pytest.raises(AttributeError):
            dest.graph_id = "other"  # type: ignore[misc]


class TestToBatchItem:
    def test_maps_all_fields(self):
        ep = Episode(
            data="hello",
            data_type="message",
            created_at="2024-06-15T10:30:00Z",
            metadata={"source_type": "slack", "channel": "general"},
        )
        item = to_batch_item(ep, Destination(graph_id="g1"))
        assert isinstance(item, BatchAddItem)
        assert item.type == "graph_episode"
        assert item.data == "hello"
        assert item.data_type == "message"
        assert item.created_at == "2024-06-15T10:30:00Z"
        assert item.metadata == {"source_type": "slack", "channel": "general"}
        assert item.graph_id == "g1"
        assert item.user_id is None

    def test_user_destination(self):
        item = to_batch_item(Episode(data="x"), Destination(user_id="u1"))
        assert item.user_id == "u1"
        assert item.graph_id is None

    def test_metadata_over_limit_rejected(self):
        metadata = {f"k{i}": i for i in range(12)}
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata=metadata)


class TestToGraphAddKwargs:
    def test_maps_all_fields(self):
        ep = Episode(
            data="hello",
            data_type="text",
            created_at="2024-06-15T10:30:00Z",
            metadata={"source_type": "document", "file_name": "handbook.md"},
        )
        kwargs = to_graph_add_kwargs(ep, Destination(user_id="u1"))
        assert kwargs == {
            "data": "hello",
            "type": "text",
            "created_at": "2024-06-15T10:30:00Z",
            "metadata": {"source_type": "document", "file_name": "handbook.md"},
            "user_id": "u1",
        }

    def test_omits_unset_optional_fields(self):
        kwargs = to_graph_add_kwargs(Episode(data="x"), Destination(graph_id="g1"))
        assert kwargs == {"data": "x", "type": "text", "graph_id": "g1"}

    def test_metadata_over_limit_rejected(self):
        metadata = {f"k{i}": i for i in range(11)}
        with pytest.raises(ConfigurationError, match="metadata"):
            Episode(data="x", metadata=metadata)
