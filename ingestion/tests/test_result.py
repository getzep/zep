"""Tests for IngestResult: status aggregation, refresh, wait, failed_items."""

from unittest.mock import MagicMock

import pytest

from tests.conftest import make_batch_summary, make_item_detail, make_item_list, make_zep_episode
from zep_ingest.exceptions import (
    ConfigurationError,
    IngestFailedError,
    IngestTimeoutError,
    IngestUntrackedError,
)
from zep_ingest.result import (
    MIN_WAIT_TIMEOUT_SECONDS,
    SECONDS_PER_SUBMITTED_ITEM,
    AddError,
    IngestResult,
    wait_timeout_seconds,
)


class TestBatchResult:
    def test_status_aggregation_priority(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "succeeded"),
            make_batch_summary("b2", "failed"),
        ]
        result.refresh()
        assert result.status == "failed"

    def test_partial_beats_processing(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "partial"),
            make_batch_summary("b2", "processing"),
        ]
        result.refresh()
        assert result.status == "partial"

    def test_all_succeeded(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "succeeded")
        result.refresh()
        assert result.status == "succeeded"

    def test_status_without_refresh_is_queued(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        assert result.status == "queued"

    def test_from_batch_ids_reconstructs_result(self, mock_zep):
        result = IngestResult.from_batch_ids(mock_zep, ["b1", "b2"])
        assert result.method == "batch"
        assert result.batch_ids == ["b1", "b2"]
        assert result.status == "queued"
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "succeeded"),
            make_batch_summary("b2", "processing"),
        ]
        result.refresh()
        assert result.status == "processing"

    def test_wait_polls_until_terminal(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b1", "processing"),
            make_batch_summary("b1", "processing"),
            make_batch_summary("b1", "succeeded"),
        ]
        returned = result.wait(poll_interval=0)
        assert returned is result
        assert result.status == "succeeded"
        assert mock_zep.batch.get.call_count == 3

    def test_wait_polls_last_batch_until_terminal_then_confirms_others(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.get.side_effect = [
            make_batch_summary("b2", "processing"),
            make_batch_summary("b2", "succeeded"),
            make_batch_summary("b1", "succeeded"),
        ]
        result.wait(poll_interval=0)
        assert [call.args[0] for call in mock_zep.batch.get.call_args_list] == ["b2", "b2", "b1"]
        assert result.status == "succeeded"

    def test_auto_timeout_scales_with_submitted_items(self, mock_zep, monkeypatch):
        clock = iter(float(seconds) for seconds in range(0, 10_000_000, 1_000_000))
        monkeypatch.setattr("zep_ingest.result.time.monotonic", lambda: next(clock))
        monkeypatch.setattr("zep_ingest.result.time.sleep", lambda _: None)
        result = IngestResult(method="batch", batch_ids=["b1"], items_submitted=10, client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "processing")

        with pytest.raises(IngestTimeoutError) as excinfo:
            result.wait(poll_interval=0)

        expected = wait_timeout_seconds(10)
        assert expected == max(MIN_WAIT_TIMEOUT_SECONDS, 10 * SECONDS_PER_SUBMITTED_ITEM)
        assert f"after {expected}s" in str(excinfo.value)

    def test_from_batch_ids_auto_timeout_is_unlimited(self, mock_zep):
        result = IngestResult.from_batch_ids(mock_zep, ["b1"])
        assert result._resolve_wait_timeout("auto") is None

    def test_wait_timeout_raises_but_result_usable(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "processing")
        with pytest.raises(IngestTimeoutError):
            result.wait(poll_interval=0, timeout=0)
        assert result.status == "processing"

    @pytest.mark.parametrize("field", ["poll_interval", "timeout"])
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_wait_timing_refused_before_polling(self, mock_zep, field, value):
        # A non-finite interval reaches time.sleep as a ValueError (nan) or an
        # OverflowError (inf), and a non-finite timeout is worse than either: it
        # silently stops being a timeout, because `elapsed >= nan` and
        # `elapsed >= inf` are never true, so wait() would poll forever.
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        with pytest.raises(ConfigurationError, match=field):
            result.wait(**{field: value})

        mock_zep.batch.get.assert_not_called()

    def test_finite_timeout_still_fires(self, mock_zep, monkeypatch):
        # the control for the case above: with a clock that leaps past any
        # deadline, a finite timeout terminates the loop on the first check
        clock = iter(float(seconds) for seconds in range(0, 10_000_000, 1_000_000))
        monkeypatch.setattr("zep_ingest.result.time.monotonic", lambda: next(clock))
        monkeypatch.setattr("zep_ingest.result.time.sleep", lambda _: None)
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "processing")

        with pytest.raises(IngestTimeoutError):
            result.wait(poll_interval=0, timeout=30.0)

    def test_failed_items_pages_across_batches(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1", "b2"], client=mock_zep)
        mock_zep.batch.list_items.side_effect = [
            make_item_list([make_item_detail(status="failed", sequence_index=3)], next_cursor=7),
            make_item_list([make_item_detail(status="failed", sequence_index=9)]),
            make_item_list([make_item_detail(status="failed", sequence_index=1)]),
        ]
        items = result.failed_items()
        assert len(items) == 3
        first_call = mock_zep.batch.list_items.call_args_list[0]
        assert first_call.args == ("b1",)
        assert first_call.kwargs["status"] == "failed"
        second_call = mock_zep.batch.list_items.call_args_list[1]
        assert second_call.kwargs["cursor"] == 7

    def test_failed_items_respects_limit(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.list_items.return_value = make_item_list(
            [make_item_detail(status="failed", sequence_index=i) for i in range(5)]
        )
        assert len(result.failed_items(limit=2)) == 2

    def test_failed_items_includes_pages_rejected_before_batch_acceptance(self, mock_zep):
        submission_error = AddError(index=0, item_count=2, error="batch.add failed", batch_id="b1")
        server_error = make_item_detail(status="failed", sequence_index=3)
        result = IngestResult(
            method="batch",
            batch_ids=["b1"],
            add_errors=[submission_error],
            client=mock_zep,
        )
        mock_zep.batch.list_items.return_value = make_item_list([server_error])

        assert result.failed_items() == [submission_error, server_error]


class TestSequentialResult:
    def test_status_processing_until_all_processed(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=False),
        ]
        result.refresh()
        assert result.status == "processing"

    def test_status_succeeded_when_all_processed(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=True),
        ]
        result.refresh()
        assert result.status == "succeeded"

    def test_refresh_skips_already_processed_uuids(self, mock_zep):
        result = IngestResult(method="sequential", episode_uuids=["e1", "e2"], client=mock_zep)
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e1", processed=True),
            make_zep_episode("e2", processed=False),
            make_zep_episode("e2", processed=True),
        ]
        result.refresh()
        result.refresh()
        assert result.status == "succeeded"
        assert mock_zep.graph.episode.get.call_count == 3

    def test_wait_polls_only_the_last_episode(self, mock_zep):
        result = IngestResult(
            method="sequential",
            episode_uuids=["e1", "e2", "e3"],
            items_submitted=3,
            client=mock_zep,
        )
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e3", processed=False),
            make_zep_episode("e3", processed=True),
        ]

        result.wait(poll_interval=0)

        assert [call.kwargs["uuid_"] for call in mock_zep.graph.episode.get.call_args_list] == [
            "e3",
            "e3",
        ]
        assert result.status == "succeeded"

    def test_wait_does_not_mark_all_episodes_done_when_multi_thread_tail_finishes_first(
        self, mock_zep
    ):
        result = IngestResult(
            method="sequential",
            episode_uuids=["e-thread-1", "e-thread-2"],
            items_submitted=2,
            client=mock_zep,
            _single_queue_episode_poll=False,
        )
        mock_zep.graph.episode.get.side_effect = [
            make_zep_episode("e-thread-1", processed=False),
            make_zep_episode("e-thread-2", processed=True),
        ]

        with pytest.raises(IngestTimeoutError):
            result.wait(poll_interval=0, timeout=0)

        assert "e-thread-1" not in result._processed_uuids

    def test_combine_then_wait_polls_the_combined_tail(self, mock_zep):
        first = IngestResult(
            method="sequential", episode_uuids=["e1"], items_submitted=1, client=mock_zep
        )
        second = IngestResult(
            method="sequential", episode_uuids=["e2"], items_submitted=1, client=mock_zep
        )
        mock_zep.graph.episode.get.return_value = make_zep_episode("e2", processed=True)

        combined = first.combine(second)
        combined.wait(poll_interval=0)

        assert combined.items_submitted == 2
        assert combined.episode_uuids == ["e1", "e2"]
        mock_zep.graph.episode.get.assert_called_once_with(uuid_="e2")
        assert combined.status == "succeeded"

    def test_combine_does_not_merge_identity_lists(self, mock_zep):
        nodes = IngestResult(
            method="sequential",
            node_uuids=["node-1"],
            task_ids=["t1"],
            client=mock_zep,
        )
        docs = IngestResult(
            method="batch",
            batch_ids=["b1"],
            edge_uuids=["edge-1"],
            client=mock_zep,
        )

        combined = nodes.combine(docs)

        assert combined.node_uuids == []
        assert combined.edge_uuids == []
        assert combined.task_ids == ["t1"]
        assert combined.batch_ids == ["b1"]

    def test_combine_rejects_different_clients(self, mock_zep):
        other = MagicMock()
        left = IngestResult(method="sequential", client=mock_zep)
        right = IngestResult(method="sequential", client=other)
        with pytest.raises(ConfigurationError, match="different Zep clients"):
            left.combine(right)

    def test_wait_polls_batch_tail_when_mixed_with_episodes(self, mock_zep):
        result = IngestResult(
            method="sequential",
            batch_ids=["b1"],
            episode_uuids=["e1"],
            items_submitted=5,
            client=mock_zep,
        )
        order: list[str] = []

        def get_episode(uuid_: str):
            order.append("episode")
            return make_zep_episode(uuid_, processed=order.count("episode") >= 2)

        def get_batch(batch_id: str):
            order.append("batch")
            if order.count("batch") <= 1:
                return make_batch_summary("b1", "processing")
            return make_batch_summary("b1", "succeeded")

        mock_zep.graph.episode.get.side_effect = get_episode
        mock_zep.batch.get.side_effect = get_batch

        result.wait(poll_interval=0)

        assert "batch" in order
        assert "episode" in order
        assert result.status == "succeeded"

    def test_wait_polls_tasks_while_batch_tail_still_in_flight(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult(
            method="sequential",
            batch_ids=["b1"],
            task_ids=["t1"],
            items_submitted=5,
            client=mock_zep,
        )
        order: list[str] = []

        def get_batch(batch_id: str):
            order.append("batch")
            if order.count("batch") == 1:
                return make_batch_summary("b1", "processing")
            return make_batch_summary("b1", "succeeded")

        def get_task(task_id: str):
            order.append("task")
            return GetTaskResponse(task_id="t1", status="succeeded")

        mock_zep.batch.get.side_effect = get_batch
        mock_zep.task.get.side_effect = get_task

        result.wait(poll_interval=0)

        assert order == ["batch", "task", "batch"]
        assert result.status == "succeeded"

    def test_add_errors_make_status_partial(self, mock_zep):
        result = IngestResult(
            method="sequential",
            episode_uuids=["e1"],
            add_errors=[AddError(index=5, item_count=1, error="boom")],
            client=mock_zep,
        )
        mock_zep.graph.episode.get.return_value = make_zep_episode("e1", processed=True)
        result.refresh()
        assert result.status == "partial"

    def test_failed_items_returns_add_errors(self, mock_zep):
        errors = [AddError(index=0, item_count=1, error="x")]
        result = IngestResult(
            method="sequential", episode_uuids=[], add_errors=errors, client=mock_zep
        )
        assert result.failed_items() == errors

    def test_empty_run_is_succeeded(self, mock_zep):
        result = IngestResult(method="sequential", client=mock_zep)
        assert result.status == "succeeded"

    def test_submitted_work_without_handles_is_untracked(self, mock_zep):
        result = IngestResult(
            method="sequential",
            items_submitted=2,
            untracked_items=2,
            client=mock_zep,
        )

        assert result.status == "untracked"
        with pytest.raises(IngestUntrackedError, match="2 submitted item"):
            result.wait()
        mock_zep.graph.episode.get.assert_not_called()
        mock_zep.task.get.assert_not_called()

    def test_untracked_work_beats_queued_tracked_work(self, mock_zep):
        result = IngestResult(
            method="sequential",
            items_submitted=2,
            task_ids=["t1"],
            untracked_items=1,
            client=mock_zep,
        )

        assert result.status == "untracked"
        with pytest.raises(IngestUntrackedError):
            result.wait()
        mock_zep.task.get.assert_not_called()

    def test_task_ids_are_polled_until_succeeded(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult(method="sequential", task_ids=["t1"], client=mock_zep)
        assert result.status == "queued"
        mock_zep.task.get.side_effect = [
            GetTaskResponse(task_id="t1", status="processing"),
            GetTaskResponse(task_id="t1", status="succeeded"),
        ]

        result.wait(poll_interval=0)

        assert result.status == "succeeded"
        assert mock_zep.task.get.call_count == 2

    def test_refresh_collects_edge_uuid_from_completed_task_params(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1", "t2"])
        mock_zep.task.get.side_effect = [
            GetTaskResponse(
                task_id="t1",
                status="succeeded",
                params={"edge_uuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"},
            ),
            GetTaskResponse(task_id="t2", status="processing"),
            GetTaskResponse(
                task_id="t2",
                status="succeeded",
                params={"edge_uuid": "ffffffff-ffff-4fff-8fff-ffffffffffff"},
            ),
        ]

        result.refresh()
        assert result.edge_uuids == ["eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"]
        assert result.status == "processing"

        result.refresh()
        assert result.edge_uuids == [
            "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
        ]
        assert result.status == "succeeded"

    def test_edge_uuids_preserve_task_ids_order_when_later_task_finishes_first(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1", "t2"])
        mock_zep.task.get.side_effect = [
            # First refresh: earlier task still running, later task already done.
            GetTaskResponse(task_id="t1", status="processing"),
            GetTaskResponse(
                task_id="t2",
                status="succeeded",
                params={"edge_uuid": "ffffffff-ffff-4fff-8fff-ffffffffffff"},
            ),
            # Second refresh: earlier task completes. t2 is terminal and skipped.
            GetTaskResponse(
                task_id="t1",
                status="succeeded",
                params={"edge_uuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"},
            ),
        ]

        result.refresh()
        # Contiguous prefix only: do not surface t2's UUID ahead of t1.
        assert result.edge_uuids == []
        assert result.status == "processing"

        result.refresh()
        assert result.edge_uuids == [
            "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
        ]
        assert result.status == "succeeded"

    def test_edge_uuids_keep_later_success_after_earlier_terminal_failure(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1", "t2"])
        mock_zep.task.get.side_effect = [
            GetTaskResponse(task_id="t1", status="failed", params={}),
            GetTaskResponse(
                task_id="t2",
                status="succeeded",
                params={"edge_uuid": "ffffffff-ffff-4fff-8fff-ffffffffffff"},
            ),
        ]

        result.refresh()

        assert result.edge_uuids == [None, "ffffffff-ffff-4fff-8fff-ffffffffffff"]
        assert result.status == "failed"

    def test_node_uuids_keep_later_success_after_earlier_terminal_failure(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1", "t2"])
        mock_zep.task.get.side_effect = [
            GetTaskResponse(task_id="t1", status="canceled", params={}),
            GetTaskResponse(
                task_id="t2",
                status="succeeded",
                params={"node_uuids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]},
            ),
        ]

        result.refresh()

        assert result.node_uuids == [None, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
        assert result.status == "canceled"

    def test_refresh_keeps_submit_time_node_uuids_when_task_params_arrive(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        # ingest_nodes already recorded response UUIDs in submission order; task
        # params must not extend or reorder that list on refresh.
        result = IngestResult(
            method="sequential",
            task_ids=["t1"],
            node_uuids=["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            client=mock_zep,
        )
        result._node_uuids_from_submit = True
        mock_zep.task.get.return_value = GetTaskResponse(
            task_id="t1",
            status="succeeded",
            params={
                "node_uuids": [
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                ]
            },
        )

        result.refresh()
        result.refresh()

        assert result.node_uuids == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]

    def test_node_uuids_from_task_params_preserve_task_ids_order(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1", "t2"])
        mock_zep.task.get.side_effect = [
            GetTaskResponse(task_id="t1", status="processing"),
            GetTaskResponse(
                task_id="t2",
                status="succeeded",
                params={"node_uuids": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]},
            ),
            GetTaskResponse(
                task_id="t1",
                status="succeeded",
                params={
                    "node_uuids": [
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
                    ]
                },
            ),
        ]

        result.refresh()
        assert result.node_uuids == []

        result.refresh()
        assert result.node_uuids == [
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ]

    def test_failed_task_makes_result_failed(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1"])
        mock_zep.task.get.return_value = GetTaskResponse(task_id="t1", status="failed")

        result.refresh()

        assert result.status == "failed"


class TestRaiseForStatus:
    def test_raises_on_failed(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "failed")
        result.refresh()
        with pytest.raises(IngestFailedError) as excinfo:
            result.raise_for_status()
        message = str(excinfo.value)
        assert "'failed'" in message
        assert "the failure happened server-side" in message
        assert "0 submission error" not in message

    def test_raises_on_canceled_batch(self, mock_zep):
        result = IngestResult(method="batch", batch_ids=["b1"], client=mock_zep)
        mock_zep.batch.get.return_value = make_batch_summary("b1", "canceled")

        result.wait(poll_interval=0)

        assert result.status == "canceled"
        with pytest.raises(IngestFailedError) as excinfo:
            result.raise_for_status()
        message = str(excinfo.value)
        assert "canceled before all items were processed" in message
        assert "0 submission error" not in message

    def test_raises_on_canceled_task(self, mock_zep):
        from zep_cloud.types.get_task_response import GetTaskResponse

        result = IngestResult.from_task_ids(mock_zep, ["t1"])
        mock_zep.task.get.return_value = GetTaskResponse(task_id="t1", status="cancelled")

        result.wait(poll_interval=0)

        assert result.status == "canceled"
        with pytest.raises(IngestFailedError, match="canceled before all items were processed"):
            result.raise_for_status()

    def test_untracked_is_not_a_failure(self, mock_zep):
        result = IngestResult(
            method="sequential", items_submitted=1, untracked_items=1, client=mock_zep
        )
        assert result.status == "untracked"
        result.raise_for_status()

    def test_raises_on_add_errors(self, mock_zep):
        result = IngestResult(
            method="sequential",
            add_errors=[AddError(index=0, item_count=1, error="x")],
            client=mock_zep,
        )
        with pytest.raises(IngestFailedError):
            result.raise_for_status()

    def test_no_raise_when_clean(self, mock_zep):
        result = IngestResult(method="sequential", client=mock_zep)
        result.raise_for_status()

    def test_add_error_never_contains_episode_body(self):
        err = AddError(index=1, item_count=1, error="API says no")
        assert not hasattr(err, "data")
        assert not hasattr(err, "episode")
