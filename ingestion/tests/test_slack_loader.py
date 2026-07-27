"""Tests for SlackExportLoader against the checked-in fixture export."""

import json
import shutil
import zipfile
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


def mixed_export(root: Path) -> Path:
    """An export carrying all four conversation indexes Slack writes."""
    export = root / "mixed_export"
    export.mkdir()
    (export / "users.json").write_text(
        json.dumps(
            [
                {"id": "U001", "name": "avery", "profile": {"display_name": "Avery Brown"}},
                {"id": "U002", "name": "blake", "profile": {"display_name": "Blake Carter"}},
                {"id": "U003", "name": "charlie", "profile": {}},
            ]
        )
    )
    (export / "channels.json").write_text(json.dumps([{"id": "C1", "name": "general"}]))
    (export / "groups.json").write_text(json.dumps([{"id": "G1", "name": "leadership"}]))
    (export / "dms.json").write_text(json.dumps([{"id": "D01ABC234", "members": ["U001", "U002"]}]))
    (export / "mpims.json").write_text(
        json.dumps(
            [
                {
                    "id": "G2",
                    "name": "mpdm-avery--blake--charlie-1",
                    "members": ["U001", "U002", "U003"],
                }
            ]
        )
    )
    conversations = {
        "general": ("U001", "public channel message", "1718355600.000100"),
        "leadership": ("U002", "private channel message", "1718355700.000100"),
        "D01ABC234": ("U001", "direct message", "1718355800.000100"),
        "mpdm-avery--blake--charlie-1": ("U003", "group dm message", "1718355900.000100"),
    }
    for folder, (user, text, ts) in conversations.items():
        (export / folder).mkdir()
        (export / folder / "2024-06-14.json").write_text(
            json.dumps([{"type": "message", "user": user, "text": text, "ts": ts}])
        )
    return export


def index_less_export(root: Path) -> Path:
    """The same export with its four conversation indexes removed: folder names
    are all that is left to type conversations by."""
    export = mixed_export(root)
    for index in ("channels.json", "groups.json", "dms.json", "mpims.json"):
        (export / index).unlink()
    return export


def wrapped_grid_export(root: Path) -> Path:
    """An Enterprise Grid export zipped inside a wrapping folder: its roster is
    org_users.json and it holds no public channels, so no channels.json."""
    archive = root / "grid.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "grid_export/org_users.json",
            json.dumps(
                [
                    {"id": "U001", "name": "avery", "profile": {"display_name": "Avery Brown"}},
                    {"id": "U002", "name": "blake", "profile": {"display_name": "Blake Carter"}},
                ]
            ),
        )
        zf.writestr("grid_export/groups.json", json.dumps([{"id": "G1", "name": "leadership"}]))
        zf.writestr(
            "grid_export/dms.json", json.dumps([{"id": "D01ABC234", "members": ["U001", "U002"]}])
        )
        zf.writestr(
            "grid_export/leadership/2024-06-14.json",
            json.dumps(
                [
                    {
                        "type": "message",
                        "user": "U002",
                        "text": "private channel message",
                        "ts": "1718355700.000100",
                    }
                ]
            ),
        )
        zf.writestr(
            "grid_export/D01ABC234/2024-06-14.json",
            json.dumps(
                [
                    {
                        "type": "message",
                        "user": "U001",
                        "text": "direct message",
                        "ts": "1718355800.000100",
                    }
                ]
            ),
        )
    return archive


class TestBasics:
    def test_all_episodes_are_text_type_with_metadata(self):
        episodes = load()
        assert episodes
        for ep in episodes:
            assert ep.data_type == "text"
            assert ep.metadata is not None
            assert ep.metadata["source_type"] == "slack"
            assert ep.metadata["channel"] in ("general", "random")

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

    def test_invalid_grouping_is_rejected_eagerly(self):
        with pytest.raises(ConfigurationError, match="grouping"):
            SlackExportLoader(FIXTURE, grouping="messages")  # type: ignore[arg-type]


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

    def test_lone_reply_keeps_thread_ts_when_its_parent_was_filtered(self, tmp_path):
        """Message count is not the test for "is a thread": a reply whose parent was
        skipped as join/bot noise is still thread-scoped and must stay filterable."""
        export = mixed_export(tmp_path)
        (export / "general" / "2024-06-15.json").write_text(
            json.dumps(
                [
                    {"subtype": "channel_join", "user": "U001", "ts": "1718400000.1", "text": "j"},
                    {
                        "user": "U001",
                        "ts": "1718400001.1",
                        "thread_ts": "1718400000.1",
                        "text": "orphan reply",
                    },
                    {"user": "U001", "ts": "1718400002.1", "text": "standalone"},
                ]
            )
        )
        episodes = list(SlackExportLoader(export).load())

        orphan = next(e for e in episodes if "orphan reply" in e.data)
        standalone = next(e for e in episodes if "standalone" in e.data)
        assert orphan.metadata["thread_ts"] == "1718400000.1"
        assert "thread_ts" not in standalone.metadata  # not part of any thread

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

    def test_bot_message_subtype_skipped_without_a_bot_id(self, tmp_path):
        """Most app posts carry a bot_id, but an incoming-webhook post often has
        only the subtype, so keying solely on bot_id lets that traffic through."""
        export = mixed_export(tmp_path)
        (export / "general" / "2024-06-15.json").write_text(
            json.dumps(
                [
                    {
                        "subtype": "bot_message",
                        "username": "CI",
                        "ts": "1718400000.1",
                        "text": "bot",
                    },
                    {"user": "U001", "ts": "1718400001.1", "text": "human"},
                ]
            )
        )
        bodies = " ".join(e.data for e in SlackExportLoader(export).load())
        assert "human" in bodies
        assert "bot" not in bodies

        included = " ".join(e.data for e in SlackExportLoader(export, include_bots=True).load())
        assert "bot" in included

    @pytest.mark.parametrize("field", ["ts", "thread_ts"])
    def test_non_numeric_timestamp_skips_one_message_not_the_export(self, tmp_path, field):
        """Ordering and created_at both read the timestamp as a float, so one bad
        value used to abort the whole export with a bare ValueError."""
        export = mixed_export(tmp_path)
        bad = {"user": "U001", "ts": "1718400001.1", "text": "bad timestamp"}
        bad[field] = "not-a-timestamp"
        (export / "general" / "2024-06-15.json").write_text(
            json.dumps([{"user": "U001", "ts": "1718400000.1", "text": "kept"}, bad])
        )
        loader = SlackExportLoader(export)

        bodies = " ".join(e.data for e in loader.load())

        assert "kept" in bodies
        assert "bad timestamp" not in bodies
        assert any("not a number" in w for w in loader.warnings)

    def test_duplicate_timestamps_are_reported_not_dropped_in_silence(self, tmp_path):
        export = mixed_export(tmp_path)
        (export / "general" / "2024-06-15.json").write_text(
            json.dumps(
                [
                    {"user": "U001", "ts": "1718400000.1", "text": "kept"},
                    {"user": "U001", "ts": "1718400000.1", "text": "duplicate timestamp"},
                ]
            )
        )
        loader = SlackExportLoader(export)
        bodies = " ".join(e.data for e in loader.load())

        assert "duplicate timestamp" not in bodies
        assert any("repeated a timestamp" in w for w in loader.warnings)

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


class TestConversationTypes:
    def test_default_ingests_public_channels_only(self, tmp_path):
        episodes = load(mixed_export(tmp_path))
        assert [e.metadata["conversation_type"] for e in episodes] == ["public_channel"]
        assert "public channel message" in episodes[0].data

    def test_skipped_types_warn_with_counts_and_remedy(self, tmp_path):
        loader = SlackExportLoader(mixed_export(tmp_path))
        list(loader.load())
        warning = next(w for w in loader.warnings if "conversation_types" in w)
        assert "1 private channel(s)" in warning
        assert "1 DM conversation(s)" in warning
        assert "1 group DM conversation(s)" in warning
        assert (
            "conversation_types=['public_channel', 'private_channel', 'dm', 'group_dm']" in warning
        )

    def test_no_skip_warning_when_export_has_only_public_channels(self):
        loader = SlackExportLoader(FIXTURE)
        list(loader.load())
        assert not any("conversation_types" in w for w in loader.warnings)

    def test_no_skip_warning_when_every_type_selected(self, tmp_path):
        loader = SlackExportLoader(
            mixed_export(tmp_path),
            conversation_types=["public_channel", "private_channel", "dm", "group_dm"],
        )
        episodes = list(loader.load())
        assert len(episodes) == 4
        assert not any("conversation_types" in w for w in loader.warnings)

    def test_dms_only_selection(self, tmp_path):
        episodes = load(mixed_export(tmp_path), conversation_types=["dm", "group_dm"])
        assert {e.metadata["conversation_type"] for e in episodes} == {"dm", "group_dm"}
        assert not any("channel message" in e.data for e in episodes)

    def test_private_channels_only_selection(self, tmp_path):
        episodes = load(mixed_export(tmp_path), conversation_types=["private_channel"])
        assert len(episodes) == 1
        assert episodes[0].metadata["channel"] == "leadership"
        assert episodes[0].data == (
            "Blake Carter (Slack #leadership, 2024-06-14 09:01 UTC): private channel message"
        )

    def test_dm_labeled_by_resolved_members(self, tmp_path):
        episodes = load(mixed_export(tmp_path), conversation_types=["dm"])
        assert episodes[0].metadata["channel"] == "Avery Brown, Blake Carter"
        assert episodes[0].data == (
            "Avery Brown (Slack DM: Avery Brown, Blake Carter, 2024-06-14 09:03 UTC): "
            "direct message"
        )

    def test_group_dm_labeled_by_members_not_mpdm_slug(self, tmp_path):
        episodes = load(mixed_export(tmp_path), conversation_types=["group_dm"])
        assert episodes[0].metadata["channel"] == "Avery Brown, Blake Carter, charlie"
        assert episodes[0].data == (
            "charlie (Slack group DM: Avery Brown, Blake Carter, charlie, "
            "2024-06-14 09:05 UTC): group dm message"
        )

    def test_dm_without_roster_falls_back_to_raw_member_ids(self, tmp_path):
        export = mixed_export(tmp_path)
        (export / "users.json").unlink()
        loader = SlackExportLoader(export, conversation_types=["dm"])
        episodes = list(loader.load())
        assert episodes[0].metadata["channel"] == "U001, U002"
        assert any("org_users.json" in w for w in loader.warnings)

    def test_dm_without_members_falls_back_to_its_id(self, tmp_path):
        export = mixed_export(tmp_path)
        (export / "dms.json").write_text(json.dumps([{"id": "D01ABC234"}]))
        episodes = load(export, conversation_types=["dm"])
        assert episodes[0].metadata["channel"] == "D01ABC234"

    def test_channels_filter_applies_within_selected_types(self, tmp_path):
        episodes = load(
            mixed_export(tmp_path),
            conversation_types=["public_channel", "private_channel"],
            channels=["leadership"],
        )
        assert len(episodes) == 1
        assert episodes[0].metadata["channel"] == "leadership"

    def test_channel_of_unselected_type_raises_with_remedy(self, tmp_path):
        with pytest.raises(ConfigurationError) as excinfo:
            load(mixed_export(tmp_path), channels=["leadership"])
        message = str(excinfo.value)
        assert "leadership" in message
        assert "Add private_channel to conversation_types" in message

    def test_dm_selectable_by_id_or_member_label(self, tmp_path):
        export = mixed_export(tmp_path)
        by_id = load(export, conversation_types=["dm"], channels=["D01ABC234"])
        by_label = load(export, conversation_types=["dm"], channels=["Avery Brown, Blake Carter"])
        assert len(by_id) == 1
        assert [e.data for e in by_id] == [e.data for e in by_label]

    def test_unknown_channel_error_lists_dm_ids_with_their_labels(self, tmp_path):
        with pytest.raises(ConfigurationError) as excinfo:
            load(mixed_export(tmp_path), channels=["nonexistent"])
        assert "D01ABC234 (Avery Brown, Blake Carter)" in str(excinfo.value)

    def test_zip_parity_across_conversation_types(self, tmp_path):
        export = mixed_export(tmp_path)
        archive = shutil.make_archive(str(tmp_path / "mixed"), "zip", export)
        types = ["public_channel", "private_channel", "dm", "group_dm"]
        from_dir = load(export, conversation_types=types)
        from_zip = load(Path(archive), conversation_types=types)
        assert [e.data for e in from_zip] == [e.data for e in from_dir]

    @pytest.mark.parametrize(
        "filename,entry",
        [
            ("groups.json", {"name": "../../outside"}),
            ("dms.json", {"id": "../../outside"}),
            ("mpims.json", {"name": "../../outside"}),
        ],
    )
    def test_path_traversal_rejected_from_every_index(self, tmp_path, filename, entry):
        export = tmp_path / "export"
        export.mkdir()
        (export / "users.json").write_text("[]")
        (export / filename).write_text(json.dumps([entry]))
        with pytest.raises(ConfigurationError, match="Invalid Slack channel path"):
            load(export, conversation_types=["private_channel", "dm", "group_dm"])

    @pytest.mark.parametrize("types", [[], ["dms"], "dm"])
    def test_invalid_conversation_types_rejected_eagerly(self, types):
        with pytest.raises(ConfigurationError, match="conversation_types"):
            SlackExportLoader(FIXTURE, conversation_types=types)


class TestFormatting:
    def test_line_format_display_name_channel_timestamp(self):
        eps = general(load())
        assert eps[0].data == "Avery Brown (Slack #general, 2024-06-14 09:00 UTC): Hello world"

    def test_real_name_wins_over_a_display_name_handle(self):
        """U001's display_name is the handle "avery" and real_name is "Avery Brown".
        Slack's own precedence would pick the handle, which then cannot merge with
        the same person written in full in an email or document."""
        eps = general(load())
        assert "Avery Brown" in eps[0].data
        assert "avery (" not in eps[0].data

    def test_name_source_fallbacks(self):
        eps = general(load())
        # Blake Carter has empty display_name -> real_name; charlie has neither -> name
        assert "Blake Carter" in eps[1].data
        assert "charlie" in eps[1].data

    def test_display_name_is_used_when_no_real_name_exists(self, tmp_path):
        export = tmp_path / "export"
        (export / "general").mkdir(parents=True)
        (export / "users.json").write_text(
            json.dumps([{"id": "U1", "name": "mo", "profile": {"display_name": "Morgan Lee"}}])
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps([{"type": "message", "user": "U1", "text": "Hi", "ts": "1718355600.000100"}])
        )
        loader = SlackExportLoader(export)
        [episode] = list(loader.load())
        assert "Morgan Lee" in episode.data
        # a full name, handle-shaped or not, is not worth warning about
        assert not any("no real_name" in w for w in loader.warnings)

    def test_user_id_is_exposed_to_a_custom_formatter(self):
        """The escape hatch for a thin roster: remap the raw Slack id yourself."""
        directory = {"U001": "Avery Q. Brown", "U002": "Blake Carter", "U003": "Charlie Diaz"}
        eps = general(load(formatter=lambda m: f"{directory[m.user_id]}: {m.text}"))
        assert eps[0].data == "Avery Q. Brown: Hello world"

    def test_bot_message_has_no_user_id(self):
        captured: list[str | None] = []

        def formatter(message):
            captured.append(message.user_id)
            return message.text

        load(include_bots=True, formatter=formatter)
        assert None in captured  # the fixture's bot post carries a username, not an id

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


class TestWeakNameWarnings:
    def test_warns_when_an_ingested_author_has_no_real_name(self):
        """charlie (U003) has neither real_name nor display_name, so the label falls
        back to the username slug — reported because it may not merge."""
        loader = SlackExportLoader(FIXTURE)
        list(loader.load())
        warning = next(w for w in loader.warnings if "no real_name" in w)
        assert "charlie" in warning
        assert "SlackMessage.user_id" in warning

    def test_no_warning_when_every_ingested_author_has_a_real_name(self, tmp_path):
        export = tmp_path / "export"
        (export / "general").mkdir(parents=True)
        (export / "users.json").write_text(
            json.dumps(
                [
                    {
                        "id": "U1",
                        "name": "mo",
                        "profile": {"display_name": "mo", "real_name": "Morgan Lee"},
                    }
                ]
            )
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps([{"type": "message", "user": "U1", "text": "Hi", "ts": "1718355600.000100"}])
        )
        loader = SlackExportLoader(export)
        list(loader.load())
        assert not any("no real_name" in w for w in loader.warnings)

    def test_a_weak_name_never_ingested_is_not_reported(self, tmp_path):
        """The roster lists a handle-only user who posts nowhere we read; warning
        about them would be noise."""
        export = tmp_path / "export"
        (export / "general").mkdir(parents=True)
        (export / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "profile": {"real_name": "Morgan Lee"}},
                    {"id": "U2", "name": "ghost", "profile": {}},
                ]
            )
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps([{"type": "message", "user": "U1", "text": "Hi", "ts": "1718355600.000100"}])
        )
        loader = SlackExportLoader(export)
        list(loader.load())
        assert not any("no real_name" in w for w in loader.warnings)

    def test_a_handle_in_a_mention_is_reported(self, tmp_path):
        """@mentions resolve through the same roster, so a handle reaches the graph
        even when that user never posted."""
        export = tmp_path / "export"
        (export / "general").mkdir(parents=True)
        (export / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "profile": {"real_name": "Morgan Lee"}},
                    {"id": "U2", "name": "riley", "profile": {"display_name": "riley"}},
                ]
            )
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps(
                [
                    {
                        "type": "message",
                        "user": "U1",
                        "text": "ask <@U2> about it",
                        "ts": "1718355600.000100",
                    }
                ]
            )
        )
        loader = SlackExportLoader(export)
        [episode] = list(loader.load())
        assert "@riley" in episode.data
        assert any("riley" in w for w in loader.warnings if "no real_name" in w)


class TestWeakNamesFromSkippedMessages:
    """@mentions resolve while text is normalized, which happens before a message
    is known to be usable. A message this run drops must not make the warning
    claim its mentions were ingested."""

    @staticmethod
    def export_with(root: Path, messages: list[dict]) -> Path:
        export = root / "export"
        (export / "general").mkdir(parents=True)
        (export / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "name": "morgan", "profile": {"real_name": "Morgan Lee"}},
                    {"id": "U2", "name": "riley", "profile": {"display_name": "riley"}},
                ]
            )
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general" / "2024-06-14.json").write_text(json.dumps(messages))
        return export

    def test_mention_in_a_message_with_an_unusable_ts_is_not_reported(self, tmp_path):
        export = self.export_with(
            tmp_path,
            [
                {"user": "U1", "text": "ask <@U2>", "ts": "not-a-number"},
                {"user": "U1", "text": "kept", "ts": "1718355600.000100"},
            ],
        )
        loader = SlackExportLoader(export)
        [episode] = list(loader.load())
        assert "riley" not in episode.data
        assert any("not a number" in w for w in loader.warnings)  # it really was dropped
        assert not any("no real_name" in w for w in loader.warnings)

    def test_mention_in_a_message_with_an_unusable_thread_ts_is_not_reported(self, tmp_path):
        export = self.export_with(
            tmp_path,
            [
                {"user": "U1", "text": "ask <@U2>", "ts": "1718355700.1", "thread_ts": "nope"},
                {"user": "U1", "text": "kept", "ts": "1718355600.000100"},
            ],
        )
        loader = SlackExportLoader(export)
        list(loader.load())
        assert not any("no real_name" in w for w in loader.warnings)

    def test_mention_in_a_duplicate_message_is_not_reported(self, tmp_path):
        """The duplicate drop happens in _load_conversation, after _parse returned."""
        export = self.export_with(
            tmp_path,
            [
                {"user": "U1", "text": "kept", "ts": "1718355600.000100"},
                {"user": "U1", "text": "dupe mentioning <@U2>", "ts": "1718355600.000100"},
            ],
        )
        loader = SlackExportLoader(export)
        list(loader.load())
        assert any("repeated a timestamp" in w for w in loader.warnings)
        assert not any("no real_name" in w for w in loader.warnings)

    def test_mention_in_a_kept_message_is_still_reported(self, tmp_path):
        """The buffer must not swallow the real case."""
        export = self.export_with(
            tmp_path, [{"user": "U1", "text": "ask <@U2>", "ts": "1718355600.000100"}]
        )
        loader = SlackExportLoader(export)
        [episode] = list(loader.load())
        assert "@riley" in episode.data
        assert any("riley" in w for w in loader.warnings if "no real_name" in w)

    def test_a_dropped_mention_does_not_mask_a_later_kept_one(self, tmp_path):
        """Buffering is per message, so an earlier drop must not clear a later hit."""
        export = self.export_with(
            tmp_path,
            [
                {"user": "U1", "text": "dropped <@U2>", "ts": "bad"},
                {"user": "U1", "text": "kept <@U2>", "ts": "1718355600.000100"},
            ],
        )
        loader = SlackExportLoader(export)
        list(loader.load())
        assert any("riley" in w for w in loader.warnings if "no real_name" in w)


class TestWeakNamesInDmLabels:
    """A DM label names its members in every episode without going through
    _resolve, so a handle-only member reaches the graph even if they never post."""

    @staticmethod
    def dm_export(root: Path) -> Path:
        export = root / "export"
        (export / "general").mkdir(parents=True)
        (export / "D01ABC234").mkdir()
        (export / "users.json").write_text(
            json.dumps(
                [
                    {"id": "U1", "name": "morgan", "profile": {"real_name": "Morgan Lee"}},
                    # handle-only, and never authors or is mentioned anywhere
                    {"id": "U2", "name": "riley", "profile": {"display_name": "riley"}},
                ]
            )
        )
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "dms.json").write_text(json.dumps([{"id": "D01ABC234", "members": ["U1", "U2"]}]))
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps(
                [{"type": "message", "user": "U1", "text": "public", "ts": "1718355600.000100"}]
            )
        )
        (export / "D01ABC234" / "2024-06-14.json").write_text(
            json.dumps(
                [{"type": "message", "user": "U1", "text": "dm text", "ts": "1718355700.000100"}]
            )
        )
        return export

    def test_handle_only_dm_member_is_reported(self, tmp_path):
        loader = SlackExportLoader(self.dm_export(tmp_path), conversation_types=["dm"])
        [episode] = list(loader.load())
        # the handle is in the episode text and metadata, so it reaches extraction
        assert "riley" in episode.data
        assert "riley" in episode.metadata["channel"]
        warning = next(w for w in loader.warnings if "no real_name" in w)
        assert "riley" in warning

    def test_a_skipped_dm_does_not_report_its_members(self, tmp_path):
        """Default conversation_types excludes DMs; warning about a conversation the
        run never read would be noise."""
        loader = SlackExportLoader(self.dm_export(tmp_path))
        episodes = list(loader.load())
        assert all("riley" not in e.data for e in episodes)
        assert not any("no real_name" in w for w in loader.warnings)

    def test_an_empty_selected_dm_does_not_report_its_members(self, tmp_path):
        """Selected but produced no episodes, so its label reached nothing."""
        export = self.dm_export(tmp_path)
        (export / "D01ABC234" / "2024-06-14.json").write_text(json.dumps([]))
        loader = SlackExportLoader(export, conversation_types=["dm"])
        assert list(loader.load()) == []
        assert not any("no real_name" in w for w in loader.warnings)


class TestExtractedWrapper:
    """A zip whose export sits in a single wrapper folder is unwrapped; the same
    export extracted to disk has to be too, or a valid export ingests nothing."""

    def test_extracted_wrapper_is_unwrapped(self, tmp_path):
        root = tmp_path / "slack-data"
        root.mkdir()
        mixed_export(root)  # creates root/mixed_export/, so root wraps the export

        loader = SlackExportLoader(root)
        episodes = list(loader.load())

        assert [e.metadata["channel"] for e in episodes] == ["general"]
        assert not any("declares no conversations" in w for w in loader.warnings)

    def test_extracted_wrapper_still_honors_the_public_only_default(self, tmp_path):
        root = tmp_path / "slack-data"
        root.mkdir()
        mixed_export(root)

        loader = SlackExportLoader(root)
        bodies = " ".join(e.data for e in loader.load())

        assert "direct message" not in bodies
        assert "private channel message" not in bodies
        assert any("not ingested" in w for w in loader.warnings)

    def test_two_top_level_folders_are_not_a_wrapper(self, tmp_path):
        root = tmp_path / "slack-data"
        root.mkdir()
        mixed_export(root)
        (root / "unrelated").mkdir()

        loader = SlackExportLoader(root)
        assert list(loader.load()) == []

    def test_zip_is_closed_even_when_the_caller_stops_early(self, tmp_path):
        """preview(limit=...) abandons the generator, so the archive has to be
        closed on teardown rather than whenever the collector gets to it."""
        archive = Path(shutil.make_archive(str(tmp_path / "export"), "zip", FIXTURE))
        loader = SlackExportLoader(archive)

        episodes = loader.load()
        next(episodes)  # consume one, then walk away
        episodes.close()

        with zipfile.ZipFile(archive) as reopened:  # not held open by the loader
            assert reopened.namelist()

    def test_summary_warnings_survive_a_caller_that_stops_early(self, tmp_path):
        """preview(limit=...) abandons the generator at a yield, so tallies appended
        after the loop would never run — a preview could show a sampled problem and
        report nothing about it."""
        export = mixed_export(tmp_path)
        (export / "users.json").unlink()  # roster gap, reported at the end
        (export / "general" / "2024-06-15.json").write_text(
            json.dumps([{"user": "U001", "ts": "not-a-timestamp", "text": "bad"}])
        )
        loader = SlackExportLoader(export)

        episodes = loader.load()
        next(episodes)
        episodes.close()

        assert any("roster" in w for w in loader.warnings)
        assert any("not a number" in w for w in loader.warnings)

    def test_warnings_do_not_accumulate_across_loads(self, tmp_path):
        loader = SlackExportLoader(mixed_export(tmp_path))

        list(loader.load())
        first = list(loader.warnings)
        list(loader.load())

        assert loader.warnings == first


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

    def test_wrapped_grid_archive_ingests_nothing_private_by_default(self, tmp_path):
        # the wrapper must be stripped even without users.json or channels.json,
        # or the whole export reads as one public pseudo-channel of DM content
        loader = SlackExportLoader(wrapped_grid_export(tmp_path))
        assert list(loader.load()) == []
        warning = next(w for w in loader.warnings if "conversation_types" in w)
        assert "1 private channel(s)" in warning
        assert "1 DM conversation(s)" in warning

    def test_wrapped_grid_archive_typed_correctly_when_requested(self, tmp_path):
        archive = wrapped_grid_export(tmp_path)
        episodes = load(archive, conversation_types=["private_channel", "dm"])
        assert [e.metadata["conversation_type"] for e in episodes] == ["private_channel", "dm"]
        assert [e.metadata["channel"] for e in episodes] == [
            "leadership",
            "Avery Brown, Blake Carter",
        ]

    @pytest.mark.parametrize(
        "marker",
        ["channels.json", "groups.json", "dms.json", "mpims.json", "users.json", "org_users.json"],
    )
    def test_wrapper_folder_stripped_for_every_index_or_roster_marker(self, tmp_path, marker):
        archive = tmp_path / "wrapped.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"wrapper/{marker}", "[]")
            zf.writestr(
                "wrapper/general/2024-06-14.json",
                json.dumps(
                    [
                        {
                            "type": "message",
                            "user": "U001",
                            "text": "public channel message",
                            "ts": "1718355600.000100",
                        }
                    ]
                ),
            )
        # unstripped, the only conversation would be the wrapper folder itself
        assert [e.metadata["channel"] for e in load(archive)] == ["general"]

    def test_nested_json_is_not_read_as_a_day_file(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        (export / "users.json").write_text("[]")
        (export / "channels.json").write_text(json.dumps([{"name": "general"}]))
        (export / "general").mkdir()
        (export / "general" / "2024-06-14.json").write_text(
            json.dumps(
                [{"type": "message", "user": "U1", "text": "day file", "ts": "1718355600.000100"}]
            )
        )
        nested = export / "general" / "canvas_in_the_conversation"
        nested.mkdir()
        (nested / "2024-06-14.json").write_text(
            json.dumps(
                [{"type": "message", "user": "U1", "text": "nested", "ts": "1718355700.000100"}]
            )
        )
        archive = shutil.make_archive(str(tmp_path / "nested"), "zip", export)
        for source in (export, Path(archive)):
            episodes = load(source)
            assert len(episodes) == 1
            assert "nested" not in episodes[0].data


class TestIndexLessExport:
    """An export with no channels.json/groups.json/dms.json/mpims.json: nothing
    states what its folders are, so types come from folder names and the
    remaining ambiguity is warned about instead of assumed away."""

    def test_dm_shaped_folders_are_not_ingested_as_public_channels(self, tmp_path):
        episodes = load(index_less_export(tmp_path))
        assert [e.metadata["channel"] for e in episodes] == ["general", "leadership"]
        assert not any("direct message" in e.data or "group dm message" in e.data for e in episodes)

    def test_undetermined_types_are_warned_about(self, tmp_path):
        loader = SlackExportLoader(index_less_export(tmp_path))
        list(loader.load())
        warning = next(w for w in loader.warnings if "could not" in w)
        assert "2 folder(s) were read as public channels" in warning

    def test_dm_shaped_folders_reported_as_skipped(self, tmp_path):
        loader = SlackExportLoader(index_less_export(tmp_path))
        list(loader.load())
        warning = next(w for w in loader.warnings if "conversation_types" in w)
        assert "1 DM conversation(s)" in warning
        assert "1 group DM conversation(s)" in warning

    def test_dm_shaped_folders_ingested_on_explicit_request(self, tmp_path):
        episodes = load(index_less_export(tmp_path), conversation_types=["dm", "group_dm"])
        assert [e.metadata["conversation_type"] for e in episodes] == ["dm", "group_dm"]
        assert [e.metadata["channel"] for e in episodes] == [
            "D01ABC234",
            "mpdm-avery--blake--charlie-1",
        ]

    def test_dm_folder_named_in_channels_reports_the_remedy(self, tmp_path):
        with pytest.raises(ConfigurationError, match="Add dm to conversation_types"):
            load(index_less_export(tmp_path), channels=["D01ABC234"])

    def test_export_of_only_dm_folders_ingests_nothing_by_default(self, tmp_path):
        export = index_less_export(tmp_path)
        shutil.rmtree(export / "general")
        shutil.rmtree(export / "leadership")
        loader = SlackExportLoader(export)
        assert list(loader.load()) == []
        assert not any("could not" in w for w in loader.warnings)  # nothing was assumed


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
