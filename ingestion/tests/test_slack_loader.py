"""Tests for SlackExportLoader against the checked-in fixture export."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.slack import SlackExportLoader
from zep_ingest.types import Episode

FIXTURE = Path(__file__).parent / "fixtures" / "slack_export"


def load(path=FIXTURE, **kwargs) -> list[Episode]:
    return list(SlackExportLoader(path, **kwargs).load())


def general(episodes: list[Episode]) -> list[Episode]:
    return [e for e in episodes if e.metadata and e.metadata.get("channel") == "general"]


def iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()


class TestBasics:
    def test_all_episodes_are_text_type_with_metadata(self):
        episodes = load()
        assert episodes
        for ep in episodes:
            assert ep.data_type == "text"
            assert ep.metadata is not None
            assert ep.metadata["source"] == "slack"
            assert ep.metadata["channel"] in ("general", "random")
            assert ep.source_description in (
                "Slack #general export",
                "Slack #random export",
            )

    def test_general_channel_episode_order_and_count(self):
        eps = general(load())
        # hello, thread (parent + 2 replies), markup message — join/bot/empty skipped
        assert len(eps) == 3
        assert "Hello world" in eps[0].data
        assert "Should we deprioritize PROTOTYPE-202?" in eps[1].data
        assert "check" in eps[2].data

    def test_created_at_is_rfc3339_from_ts(self):
        eps = general(load())
        assert eps[0].created_at == iso("1718355600.000100")

    def test_missing_export_path_raises_eagerly(self):
        with pytest.raises(ConfigurationError):
            SlackExportLoader(FIXTURE / "does-not-exist")


class TestThreadGrouping:
    def test_thread_grouped_into_one_episode_across_day_files(self):
        thread = general(load())[1]
        lines = thread.data.split("\n")
        assert len(lines) == 3  # parent + reply + cross-day reply (broadcast deduped)
        assert "Should we deprioritize PROTOTYPE-202?" in lines[0]
        assert "yes, let's do that" in lines[1]
        assert "Actually PROTOTYPE-202 stays active" in lines[2]

    def test_thread_created_at_is_parent_ts(self):
        thread = general(load())[1]
        assert thread.created_at == iso("1718356000.000200")

    def test_thread_metadata_has_thread_ts(self):
        thread = general(load())[1]
        assert thread.metadata["thread_ts"] == "1718356000.000200"

    def test_message_grouping_yields_one_episode_per_message(self):
        eps = general(load(grouping="message"))
        # hello + 3 thread messages + markup (broadcast deduped, join/bot/empty skipped)
        assert len(eps) == 5
        reply = next(e for e in eps if "yes, let's do that" in e.data)
        assert reply.created_at == iso("1718356100.000300")


class TestFiltering:
    def test_bots_skipped_by_default_included_on_request(self):
        assert not any("Build passed" in e.data for e in load())
        included = load(include_bots=True)
        bot_ep = next(e for e in included if "Build passed" in e.data)
        assert "CI Bot" in bot_ep.data

    def test_join_subtype_skipped(self):
        assert not any("has joined" in e.data for e in load())

    def test_empty_messages_skipped(self):
        for ep in load(grouping="message"):
            assert ep.data.strip()

    def test_channel_filter(self):
        episodes = load(channels=["random"])
        assert len(episodes) == 1
        assert "Random note" in episodes[0].data
        assert episodes[0].metadata["channel"] == "random"


class TestFormatting:
    def test_line_format_display_name_channel_timestamp(self):
        eps = general(load())
        assert eps[0].data == "Avery Brown (Slack #general, 2024-06-14 09:00 UTC): Hello world"

    def test_display_name_fallbacks(self):
        eps = general(load())
        # Blake Carter has empty display_name -> real_name; charlie has neither -> name
        assert "Blake Carter" in eps[1].data
        assert "charlie" in eps[1].data

    def test_markup_normalization(self):
        markup = general(load())[2].data
        assert markup == (
            "Blake Carter (Slack #general, 2024-06-15 09:08 UTC): "
            "@Avery Brown and @U999 check #random @here & see "
            "the doc (https://example.com) or https://plain.example.com"
        )

    def test_custom_formatter(self):
        eps = general(load(formatter=lambda m: f"{m.sender}: {m.text}"))
        assert eps[0].data == "Avery Brown: Hello world"


class TestZipAndFallbacks:
    def test_zip_parity(self, tmp_path):
        archive = shutil.make_archive(str(tmp_path / "export"), "zip", FIXTURE)
        from_dir = load()
        from_zip = load(Path(archive))
        assert [e.data for e in from_zip] == [e.data for e in from_dir]
        assert [e.created_at for e in from_zip] == [e.created_at for e in from_dir]

    def test_directory_listing_fallback_without_channels_json(self, tmp_path):
        copy = tmp_path / "export"
        shutil.copytree(FIXTURE, copy)
        (copy / "channels.json").unlink()
        episodes = load(copy)
        channels = {e.metadata["channel"] for e in episodes}
        assert channels == {"general", "random"}

    def test_unknown_channel_filter_raises(self):
        with pytest.raises(ConfigurationError):
            load(channels=["nonexistent"])


class TestJsonExportEdgeCases:
    def test_channel_path_traversal_is_rejected(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        (export / "users.json").write_text("[]")
        (export / "channels.json").write_text(json.dumps([{"name": "../../outside"}]))
        with pytest.raises(ConfigurationError, match="Invalid Slack channel path"):
            load(export)

    def test_channel_symlink_outside_export_is_rejected(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        (export / "users.json").write_text("[]")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "2024-01-01.json").write_text("[]")
        (export / "general").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ConfigurationError, match="escapes its root"):
            load(export)

    def test_unparseable_day_file_raises_configuration_error(self, tmp_path):
        copy = tmp_path / "export"
        shutil.copytree(FIXTURE, copy)
        (copy / "general" / "2024-06-14.json").write_text("not json")
        with pytest.raises(ConfigurationError):
            load(copy)

    def test_extra_skip_subtypes_respected(self):
        eps = load(skip_subtypes=frozenset({"channel_join", "thread_broadcast"}))
        assert not any("has joined" in e.data for e in eps)

    def test_messages_sorted_even_if_day_files_unsorted(self, tmp_path):
        copy = tmp_path / "export"
        copy.mkdir()
        (copy / "users.json").write_text(json.dumps([]))
        (copy / "c1").mkdir()
        (copy / "c1" / "2024-01-01.json").write_text(
            json.dumps(
                [
                    {"type": "message", "user": "U9", "text": "second", "ts": "200.0"},
                    {"type": "message", "user": "U9", "text": "first", "ts": "100.0"},
                ]
            )
        )
        episodes = load(copy)
        assert "first" in episodes[0].data
        assert "second" in episodes[1].data


class TestUserResolution:
    def test_org_users_json_roster_resolves_names(self, tmp_path):
        # Enterprise Grid organization exports name the roster org_users.json.
        copy = tmp_path / "export"
        shutil.copytree(FIXTURE, copy)
        (copy / "users.json").rename(copy / "org_users.json")
        eps = general(load(copy))
        assert eps[0].data == "Avery Brown (Slack #general, 2024-06-14 09:00 UTC): Hello world"

    def test_missing_roster_warns_and_falls_back_to_raw_ids(self, tmp_path):
        copy = tmp_path / "export"
        shutil.copytree(FIXTURE, copy)
        (copy / "users.json").unlink()
        loader = SlackExportLoader(copy)
        episodes = list(loader.load())
        assert episodes  # still ingestible, no crash
        assert not any("Avery Brown" in e.data for e in episodes)  # names no longer resolve
        assert any("org_users.json" in w for w in loader.warnings)

    def test_unresolved_user_ids_warn(self):
        # the fixture mentions <@U999>, a user absent from users.json
        loader = SlackExportLoader(FIXTURE)
        list(loader.load())
        assert any("absent from the roster" in w for w in loader.warnings)

    def test_input_that_is_not_a_slack_export_raises(self, tmp_path):
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        (bogus / "notes.txt").write_text("hello")
        with pytest.raises(ConfigurationError):
            list(SlackExportLoader(bogus).load())
