"""Tests for LimitGuard — the always-on 10k-character safety net."""

import json

from zep_ingest.transforms.limits import LimitGuard
from zep_ingest.types import MAX_EPISODE_CHARS, SAFE_EPISODE_CHARS, Episode


def apply(guard: LimitGuard, *episodes: Episode) -> list[Episode]:
    return list(guard.apply(episodes))


class TestPassThrough:
    def test_small_episodes_untouched(self):
        eps = [
            Episode(data="text"),
            Episode(data="a: b", data_type="message"),
            Episode(data='{"a": 1}', data_type="json"),
        ]
        out = apply(LimitGuard(), *eps)
        assert [e.data for e in out] == ["text", "a: b", '{"a": 1}']


class TestSinglePieceShrink:
    def test_whitespace_padded_text_yields_shrunk_piece_not_original(self):
        # splitting can collapse an over-limit episode into ONE piece; the
        # piece must be yielded, never the original over-limit data
        episode = Episode(data="a" * 100 + " " * 200, data_type="text")
        [out] = apply(LimitGuard(limit=150), episode)
        assert len(out.data) <= 150

    def test_compact_json_rerender_yields_piece_without_split_warning(self):
        payload = json.dumps({"key": "value"}, indent=40)  # 58 chars pretty-printed
        assert len(payload) > 50
        guard = LimitGuard(limit=50)
        [out] = apply(guard, Episode(data=payload, data_type="json"))
        assert len(out.data) <= 50
        assert not any("split" in w for w in guard.warnings)  # nothing was split


class TestTextSplitting:
    def test_oversize_text_split_under_safe_limit(self):
        text = "\n\n".join("word " * 200 for _ in range(15))
        assert len(text) > SAFE_EPISODE_CHARS
        out = apply(LimitGuard(), Episode(data=text))
        assert len(out) > 1
        assert all(len(e.data) <= SAFE_EPISODE_CHARS for e in out)

    def test_nothing_ever_exceeds_hard_limit(self):
        pathological = "a" * 25_000
        out = apply(LimitGuard(), Episode(data=pathological))
        assert all(len(e.data) <= MAX_EPISODE_CHARS for e in out)


class TestMessageSplitting:
    def test_message_split_on_line_boundaries(self):
        lines = [f"Avery Brown (Slack #general): message number {i}" for i in range(400)]
        data = "\n".join(lines)
        assert len(data) > SAFE_EPISODE_CHARS
        out = apply(LimitGuard(), Episode(data=data, data_type="message"))
        assert len(out) > 1
        for episode in out:
            assert all(line in lines for line in episode.data.split("\n"))
        # no message lost
        assert sum(len(e.data.split("\n")) for e in out) == len(lines)


class TestJsonSplitting:
    def test_json_array_split_top_level_with_warning(self):
        records = [{"id": i, "text": "x" * 200} for i in range(100)]
        data = json.dumps(records)
        assert len(data) > SAFE_EPISODE_CHARS
        guard = LimitGuard()
        out = apply(guard, Episode(data=data, data_type="json"))
        assert len(out) > 1
        assert all(len(e.data) <= MAX_EPISODE_CHARS for e in out)
        # each piece is still valid JSON
        for episode in out:
            json.loads(episode.data)
        assert any("json" in w.lower() for w in guard.warnings)

    def test_json_object_split_top_level(self):
        obj = {f"key{i}": "v" * 300 for i in range(60)}
        data = json.dumps(obj)
        guard = LimitGuard()
        out = apply(guard, Episode(data=data, data_type="json"))
        assert len(out) > 1
        merged = {}
        for episode in out:
            merged.update(json.loads(episode.data))
        assert merged == obj

    def test_invalid_json_hard_split_with_warning(self):
        guard = LimitGuard()
        out = apply(guard, Episode(data="not json " * 2000, data_type="json"))
        assert all(len(e.data) <= MAX_EPISODE_CHARS for e in out)
        assert all(e.data_type == "text" for e in out)
        assert any("json" in w.lower() for w in guard.warnings)

    def test_single_oversize_json_value_stays_valid(self):
        value = "x" * 500
        guard = LimitGuard(limit=100)

        out = apply(guard, Episode(data=json.dumps({"blob": value}), data_type="json"))

        assert len(out) > 1
        assert all(len(e.data) <= 100 and e.data_type == "json" for e in out)
        assert "".join(json.loads(e.data)["blob"] for e in out) == value

    def test_unrepresentable_json_wrapper_falls_back_to_text_without_data_loss(self):
        data = json.dumps({"x" * 150: ""})
        guard = LimitGuard(limit=100)

        out = apply(guard, Episode(data=data, data_type="json"))

        assert all(e.data_type == "text" and len(e.data) <= 100 for e in out)
        assert "".join(e.data for e in out) == data


class TestFieldPropagation:
    def test_split_pieces_inherit_fields_and_part_index(self):
        text = "word " * 3000
        ep = Episode(
            data=text,
            created_at="2024-06-15T10:30:00Z",
            metadata={"source": "slack"},
            source_description="desc",
        )
        out = apply(LimitGuard(), ep)
        n = len(out)
        assert n > 1
        for i, episode in enumerate(out, start=1):
            assert episode.created_at == "2024-06-15T10:30:00Z"
            assert episode.metadata is not None
            assert episode.metadata["source"] == "slack"
            assert episode.metadata["part"] == f"{i}/{n}"
            assert episode.source_description == "desc"

    def test_full_metadata_map_is_preserved_without_part_key(self):
        metadata = {f"k{i}": i for i in range(10)}
        guard = LimitGuard(limit=100)

        out = apply(guard, Episode(data="word " * 100, metadata=metadata))

        assert len(out) > 1
        assert all(e.metadata == metadata for e in out)
        assert any("part" in warning for warning in guard.warnings)
