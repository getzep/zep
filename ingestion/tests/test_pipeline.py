"""Tests for Pipeline, preview, preflights, and the convenience one-liners."""

from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from zep_cloud.errors.not_found_error import NotFoundError

from tests.conftest import make_zep_episode
from zep_ingest.exceptions import ConfigurationError
from zep_ingest.pipeline import (
    Pipeline,
    ingest,
    ingest_documents,
    ingest_json_records,
    ingest_slack_export,
)
from zep_ingest.types import MAX_EPISODE_CHARS, Episode

FIXTURE = Path(__file__).parent / "fixtures" / "slack_export"


class ListLoader:
    def __init__(self, episodes: list[Episode]):
        self.episodes = episodes
        self.consumed = 0

    def load(self) -> Iterator[Episode]:
        for episode in self.episodes:
            self.consumed += 1
            yield episode


class UppercaseTransform:
    def __init__(self):
        self.warnings: list[str] = []

    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for ep in episodes:
            self.warnings.append("uppercased one")
            yield Episode(data=ep.data.upper(), created_at=ep.created_at)


class WarningLoader(ListLoader):
    """A loader that records a warning while loading (like JsonRecordsLoader)."""

    def __init__(self, episodes: list[Episode]):
        super().__init__(episodes)
        self.warnings: list[str] = []

    def load(self) -> Iterator[Episode]:
        self.warnings.append("loader noticed something")
        yield from super().load()


def stamped(data: str) -> Episode:
    return Episode(data=data, created_at="2024-01-01T00:00:00Z")


class TestRun:
    def test_end_to_end_batch(self, mock_zep):
        loader = ListLoader([stamped("one"), stamped("two")])
        result = Pipeline(loader, transforms=(UppercaseTransform(),)).run(mock_zep, graph_id="g1")
        assert result.method == "batch"
        assert result.items_submitted == 2
        [add_call] = mock_zep.batch.add.call_args_list
        assert [i.data for i in add_call.kwargs["items"]] == ["ONE", "TWO"]

    def test_custom_submitter_is_supported(self, mock_zep):
        class CustomSubmitter:
            def __init__(self):
                self.seen = []

            def submit(self, episodes, destination):
                self.seen = list(episodes)
                return __import__("zep_ingest").IngestResult(method="sequential")

        submitter = CustomSubmitter()
        result = Pipeline(ListLoader([stamped("x")]), submitter=submitter).run(
            mock_zep, graph_id="g1"
        )
        assert result.method == "sequential"
        assert [episode.data for episode in submitter.seen] == ["x"]
        mock_zep.batch.create.assert_not_called()

    def test_custom_submitter_rejects_builtin_dispatch_options(self, mock_zep):
        class CustomSubmitter:
            def submit(self, episodes, destination):
                raise AssertionError("must fail before submission")

        with pytest.raises(ConfigurationError, match="custom Pipeline submitter"):
            Pipeline(ListLoader([stamped("x")]), submitter=CustomSubmitter()).run(
                mock_zep, graph_id="g1", method="batch"
            )

    def test_transform_warnings_collected(self, mock_zep):
        result = Pipeline(ListLoader([stamped("x")]), transforms=(UppercaseTransform(),)).run(
            mock_zep, graph_id="g1"
        )
        assert "uppercased one" in result.warnings

    def test_preview_then_run_does_not_duplicate_warnings(self, mock_zep):
        pipeline = Pipeline(ListLoader([stamped("x")]), transforms=(UppercaseTransform(),))
        pipeline.preview()
        result = pipeline.run(mock_zep, graph_id="g1")
        assert result.warnings.count("uppercased one") == 1

    def test_loader_warnings_collected(self, mock_zep):
        result = Pipeline(WarningLoader([stamped("x")])).run(mock_zep, graph_id="g1")
        assert "loader noticed something" in result.warnings

    def test_loader_warnings_in_preview_and_not_duplicated_in_run(self, mock_zep):
        pipeline = Pipeline(WarningLoader([stamped("x")]))
        report = pipeline.preview()
        assert "loader noticed something" in report.warnings
        result = pipeline.run(mock_zep, graph_id="g1")
        assert result.warnings.count("loader noticed something") == 1

    def test_limit_guard_always_applied(self, mock_zep):
        loader = ListLoader([stamped("word " * 4000)])
        Pipeline(loader).run(mock_zep, graph_id="g1")
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert len(items) > 1
        assert all(len(i.data) <= MAX_EPISODE_CHARS for i in items)

    def test_sequential_method(self, mock_zep):
        mock_zep.graph.add.return_value = make_zep_episode("e1")
        result = Pipeline(ListLoader([stamped("x")])).run(
            mock_zep, user_id="u1", method="sequential"
        )
        assert result.method == "sequential"
        mock_zep.batch.create.assert_not_called()

    def test_invalid_late_jsonl_row_is_found_before_any_submission(self, mock_zep, tmp_path):
        source = tmp_path / "records.jsonl"
        source.write_text('{"id": 1}\n{"id": 2}\nnot-json\n')

        with pytest.raises(ConfigurationError, match="Unparseable records"):
            ingest_json_records(mock_zep, source, graph_id="g1", method="sequential")

        mock_zep.graph.add.assert_not_called()
        mock_zep.batch.create.assert_not_called()

    def test_missing_created_at_warning(self, mock_zep):
        loader = ListLoader([Episode(data="a"), Episode(data="b"), stamped("c")])
        result = Pipeline(loader).run(mock_zep, graph_id="g1")
        [warning] = [w for w in result.warnings if "created_at" in w]
        assert "2" in warning

    def test_wait_polls(self, mock_zep):
        result = Pipeline(ListLoader([stamped("x")])).run(
            mock_zep, graph_id="g1", wait=True, poll_interval=0
        )
        assert result.status == "succeeded"
        mock_zep.batch.get.assert_called()


class TestPreview:
    def test_no_client_calls_and_limit(self, mock_zep):
        loader = ListLoader([stamped(f"ep {i}") for i in range(100)])
        report = Pipeline(loader).preview(limit=5)
        assert len(report.episodes) == 5
        assert loader.consumed <= 6  # lazy: at most limit + 1 pulled
        mock_zep.batch.create.assert_not_called()
        mock_zep.graph.add.assert_not_called()

    def test_preview_surfaces_warnings(self):
        loader = ListLoader([Episode(data="no timestamp")])
        report = Pipeline(loader).preview()
        assert any("created_at" in w for w in report.warnings)

    def test_preview_applies_transforms_and_limit_guard(self):
        loader = ListLoader([stamped("word " * 4000)])
        report = Pipeline(loader).preview()
        assert len(report.episodes) > 1
        assert all(len(e.data) <= MAX_EPISODE_CHARS for e in report.episodes)

    def test_limited_preview_surfaces_alias_counts(self):
        from zep_ingest.transforms.canonicalizer import AliasCanonicalizer

        loader = ListLoader([stamped(f"ep {i}: PROTOTYPE-202 is ready") for i in range(20)])
        canon = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]})
        report = Pipeline(loader, transforms=(canon,)).preview(limit=10)
        [warning] = [w for w in report.warnings if "PROTOTYPE-202" in w]
        assert "10" in warning

    def test_limited_preview_counts_do_not_leak_into_run(self, mock_zep):
        from zep_ingest.transforms.canonicalizer import AliasCanonicalizer

        loader = ListLoader([stamped(f"ep {i}: PROTOTYPE-202 is ready") for i in range(20)])
        canon = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]})
        pipeline = Pipeline(loader, transforms=(canon,))
        pipeline.preview(limit=10)
        result = pipeline.run(mock_zep, graph_id="g1")
        [warning] = [w for w in result.warnings if "PROTOTYPE-202" in w]
        assert "20" in warning


class TestPreflights:
    def test_ontology_set_before_submission(self, mock_zep):
        entities = {"Product": object()}
        Pipeline(ListLoader([stamped("x")])).run(
            mock_zep, graph_id="g1", ontology={"entities": entities}
        )
        mock_zep.graph.set_ontology.assert_called_once()
        assert mock_zep.graph.set_ontology.call_args.kwargs["graph_ids"] == ["g1"]
        calls = [c[0] for c in mock_zep.mock_calls]
        assert calls.index("graph.set_ontology") < calls.index("batch.create")

    def test_ontology_scoped_to_user(self, mock_zep):
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, user_id="u1", ontology={"entities": {}})
        assert mock_zep.graph.set_ontology.call_args.kwargs["user_ids"] == ["u1"]

    def test_no_ontology_no_call(self, mock_zep):
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, graph_id="g1")
        mock_zep.graph.set_ontology.assert_not_called()

    def test_create_if_missing_graph(self, mock_zep):
        mock_zep.graph.get.side_effect = NotFoundError(body=None)
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, graph_id="g1", create_if_missing=True)
        mock_zep.graph.create.assert_called_once_with(graph_id="g1")

    def test_create_if_missing_user(self, mock_zep):
        mock_zep.user.get.side_effect = NotFoundError(body=None)
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, user_id="u1", create_if_missing=True)
        mock_zep.user.add.assert_called_once_with(user_id="u1")

    def test_create_if_missing_skips_existing(self, mock_zep):
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, graph_id="g1", create_if_missing=True)
        mock_zep.graph.create.assert_not_called()

    def test_no_preflight_check_without_create_if_missing(self, mock_zep):
        Pipeline(ListLoader([stamped("x")])).run(mock_zep, graph_id="g1")
        mock_zep.graph.get.assert_not_called()


class TestConvenience:
    def test_ingest_function(self, mock_zep):
        result = ingest(mock_zep, ListLoader([stamped("x")]), graph_id="g1")
        assert result.items_submitted == 1

    def test_ingest_slack_export(self, mock_zep):
        result = ingest_slack_export(mock_zep, FIXTURE, graph_id="g1")
        assert result.method == "batch"
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert all(i.data_type == "text" for i in items)
        assert all(i.created_at is not None for i in items)
        assert result.items_submitted == len(items) == 4

    def test_ingest_slack_export_channel_filter(self, mock_zep):
        result = ingest_slack_export(mock_zep, FIXTURE, graph_id="g1", channels=["random"])
        assert result.items_submitted == 1

    def test_ingest_documents_chunks_long_files(self, mock_zep, tmp_path):
        (tmp_path / "doc.md").write_text("\n\n".join("sentence here. " * 20 for _ in range(10)))
        result = ingest_documents(mock_zep, str(tmp_path / "*.md"), graph_id="kb")
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert result.items_submitted == len(items) > 1
        assert all(len(i.data) <= 500 for i in items)
        assert all(i.data_type == "text" for i in items)

    def test_ingest_slack_export_skip_subtypes(self, mock_zep):
        result = ingest_slack_export(mock_zep, FIXTURE, graph_id="g1", skip_subtypes=frozenset())
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert result.items_submitted == 5
        assert any("has joined the channel" in i.data for i in items)

    def test_ingest_slack_export_risky_words_guard(self, mock_zep):
        from zep_ingest.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError):
            ingest_slack_export(
                mock_zep,
                FIXTURE,
                graph_id="g1",
                aliases={"William Example": ["Will"]},
                risky_words=frozenset({"will"}),
            )
        mock_zep.batch.create.assert_not_called()

    def test_destination_validation_before_any_call(self, mock_zep):
        from zep_ingest.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError):
            ingest(mock_zep, ListLoader([stamped("x")]))
        mock_zep.batch.create.assert_not_called()
