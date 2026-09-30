"""Tests for ThreadMessage validation and ingest_thread_messages (user data)."""

import json

import httpx
import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.types.add_messages_result import AddMessagesResult
from zep_cloud.types.batch import Batch

from tests.conftest import BATCH_UUID, THREAD_UUID, make_batch_summary, make_task
from zep_ingest.exceptions import (
    BatchUnavailableError,
    ConfigurationError,
    IngestUntrackedError,
    InvalidBatchResponseError,
)
from zep_ingest.threads import MAX_MESSAGE_CHARS, ThreadMessage, ingest_thread_messages
from zep_ingest.types import MAX_MESSAGES_PER_THREAD_ADD

OTHER_THREAD_UUID = "77777777-7777-4777-8777-777777777777"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda _: None)


def message(**overrides) -> ThreadMessage:
    kwargs = {
        "thread_uuid": THREAD_UUID,
        "role": "user",
        "content": "My dashboard isn't loading.",
        "name": "Avery Brown",
    }
    kwargs.update(overrides)
    return ThreadMessage(**kwargs)


class TestValidation:
    def test_valid_message_constructs(self):
        message()

    @pytest.mark.parametrize("role", ["user", "assistant", "system", "function", "tool", "norole"])
    def test_all_documented_roles_accepted(self, role):
        message(role=role)

    def test_unknown_role_raises(self):
        with pytest.raises(ConfigurationError, match="role"):
            message(role="customer")

    def test_empty_content_raises(self):
        with pytest.raises(ConfigurationError, match="content"):
            message(content="   ")

    def test_empty_thread_uuid_raises(self):
        with pytest.raises(ConfigurationError, match="thread_uuid"):
            message(thread_uuid="")

    def test_thread_name_instead_of_uuid_raises(self):
        # v4 addresses a thread by the UUID that thread.create returned, so a
        # v3 thread_id name must fail here rather than reach the API.
        with pytest.raises(ConfigurationError, match="not a valid UUID"):
            message(thread_uuid="support-42")

    def test_bad_timestamp_raises(self):
        with pytest.raises(ConfigurationError, match="created_at"):
            message(created_at="June 5th")

    def test_non_string_timestamp_raises(self):
        # e.g. a JSONL row carrying an epoch number instead of an RFC3339 string
        with pytest.raises(ConfigurationError, match="created_at"):
            message(created_at=1718400000)

    def test_metadata_over_ten_keys_raises(self):
        with pytest.raises(ConfigurationError, match="metadata"):
            message(metadata={f"k{i}": i for i in range(11)})

    def test_missing_name_raises(self):
        with pytest.raises(ConfigurationError, match="name"):
            message(name=None)

    def test_created_at_is_optional(self):
        assert message().created_at is None


class TestBatchPath:
    def test_batch_items_mapped_and_processed(self, mock_zep):
        result = ingest_thread_messages(
            mock_zep,
            [message(), message(role="assistant", name="Bot")],
            method="batch",
        )
        assert result.method == "batch"
        assert result.items_submitted == 2
        items = mock_zep.batch.add_items.call_args.kwargs["items"]
        assert all(i.type == "thread_message" for i in items)
        assert items[0].thread_uuid == THREAD_UUID
        assert items[0].content == "My dashboard isn't loading."
        assert items[0].role == "user"
        assert items[0].name == "Avery Brown"
        mock_zep.batch.process.assert_called_once()

    def test_no_user_or_thread_is_created_or_looked_up(self, mock_zep):
        # The application resolves identity one time and stores the UUIDs, so
        # ingestion does no runtime lookup and creates nothing.
        ingest_thread_messages(mock_zep, [message()], method="batch")
        mock_zep.user.create.assert_not_called()
        mock_zep.thread.create.assert_not_called()

    def test_reference_time_loss_is_reported_as_a_warning(self, mock_zep):
        result = ingest_thread_messages(
            mock_zep, [message(created_at="2024-06-15T10:30:00Z")], method="batch"
        )
        assert any("created_at" in warning for warning in result.warnings)

    def test_sequential_sends_metadata(self, mock_zep):
        msgs = [message(metadata={"source": "zendesk"})]
        ingest_thread_messages(mock_zep, msgs, method="sequential")
        [sent] = mock_zep.thread.add_messages.call_args.kwargs["messages"]
        assert sent.metadata == {"source": "zendesk"}

    def test_oversize_content_split_with_warning(self, mock_zep):
        long = "A perfectly normal sentence. " * 300  # ~8700 chars
        result = ingest_thread_messages(mock_zep, [message(content=long)], method="batch")
        items = mock_zep.batch.add_items.call_args.kwargs["items"]
        assert len(items) > 1
        assert all(len(i.content) <= MAX_MESSAGE_CHARS for i in items)
        assert any("split" in w.lower() for w in result.warnings)

    def test_process_failure_recorded_not_raised(self, mock_zep):
        mock_zep.batch.process.side_effect = ApiError(status_code=500, body="boom")
        result = ingest_thread_messages(mock_zep, [message()], method="batch")
        assert result.items_submitted == 1
        [error] = result.add_errors
        assert error.batch_id == BATCH_UUID
        assert "process" in error.error
        assert result.status == "failed"

    def test_transient_create_error_is_retried(self, mock_zep):
        mock_zep.batch.create.side_effect = [
            ApiError(status_code=429, body="rate limited"),
            make_batch_summary(BATCH_UUID, "draft"),
        ]
        result = ingest_thread_messages(mock_zep, [message()], method="batch")
        assert result.items_submitted == 1
        assert mock_zep.batch.create.call_count == 2
        mock_zep.batch.add_items.assert_called_once()

    def test_server_error_on_create_propagates(self, mock_zep):
        # batch.create is non-idempotent: a 5xx may have created a batch before
        # the response was lost, so it must surface instead of being retried
        mock_zep.batch.create.side_effect = ApiError(status_code=500, body="boom")
        with pytest.raises(ApiError):
            ingest_thread_messages(mock_zep, [message()], method="batch")
        assert mock_zep.batch.create.call_count == 1
        mock_zep.batch.add_items.assert_not_called()

    def test_create_transport_error_refuses_to_submit(self, mock_zep):
        # no response arrived, so a batch may exist without a usable id; opening
        # a second one could duplicate the import
        mock_zep.batch.create.side_effect = httpx.ReadTimeout("response never arrived")
        with pytest.raises(InvalidBatchResponseError, match="may already have been created"):
            ingest_thread_messages(mock_zep, [message()], method="batch")
        mock_zep.batch.add_items.assert_not_called()

    @pytest.mark.parametrize("batch_uuid", [None, "", "   "])
    def test_missing_created_batch_uuid_fails_before_add(self, mock_zep, batch_uuid):
        mock_zep.batch.create.return_value = Batch(uuid_=batch_uuid, status="draft")

        with pytest.raises(InvalidBatchResponseError, match="batch uuid"):
            ingest_thread_messages(mock_zep, [message()], method="batch")

        mock_zep.batch.add_items.assert_not_called()


class TestSequentialPath:
    def test_auto_uses_batch(self, mock_zep):
        result = ingest_thread_messages(mock_zep, [message()])
        assert result.method == "batch"

    def test_wait_polls_the_returned_task(self, mock_zep):
        result = ingest_thread_messages(mock_zep, [message()], method="sequential")
        result.wait(poll_interval=0)
        assert result.status == "succeeded"
        mock_zep.task.get.assert_called()

    def test_auto_falls_back_to_sequential_when_endpoint_not_found(self, mock_zep):
        # a 404 means this deployment does not serve the batch endpoint, which
        # sequential thread.add_messages is unaffected by
        mock_zep.batch.create.side_effect = ApiError(status_code=404)
        result = ingest_thread_messages(mock_zep, [message()])
        assert result.method == "sequential"
        assert any("not available" in w for w in result.warnings)
        # a missing endpoint is conclusive, so it is never retried
        assert mock_zep.batch.create.call_count == 1

    @pytest.mark.parametrize("status_code", [402, 403])
    def test_auto_refused_create_propagates_without_fallback(self, mock_zep, status_code):
        # a refused key or an exhausted quota would refuse thread.add_messages
        # too, so falling back would only bury the real error in a slow run
        mock_zep.batch.create.side_effect = ApiError(status_code=status_code, body="refused")
        with pytest.raises(ApiError) as caught:
            ingest_thread_messages(mock_zep, [message()])
        assert not isinstance(caught.value, BatchUnavailableError)
        assert caught.value.status_code == status_code
        assert caught.value.body == "refused"
        mock_zep.thread.add_messages.assert_not_called()

    def test_auto_retries_transient_create_error_instead_of_falling_back(self, mock_zep):
        # only a missing endpoint may downgrade the run to sequential; a 429 is
        # a blip, so the retry keeps the run on the batch path
        mock_zep.batch.create.side_effect = [
            ApiError(status_code=429, body="rate limited"),
            make_batch_summary(BATCH_UUID, "draft"),
        ]
        result = ingest_thread_messages(mock_zep, [message()])
        assert result.method == "batch"
        assert result.items_submitted == 1
        mock_zep.thread.add_messages.assert_not_called()

    def test_auto_no_sequential_fallback_after_partial_batch(self, mock_zep, monkeypatch):
        # once some batches have been submitted, a mid-stream create failure must
        # stop the run — never fall back to sequential, which would re-submit the
        # messages already in flight — and must keep the ids of those batches
        monkeypatch.setattr("zep_ingest.threads.MAX_ITEMS_PER_ADD", 1)
        monkeypatch.setattr("zep_ingest.threads.DEFAULT_ITEMS_PER_BATCH", 1)
        mock_zep.batch.create.side_effect = [
            make_batch_summary(BATCH_UUID, "draft"),
            ApiError(status_code=404),
        ]
        msgs = [message(content="m0"), message(content="m1")]

        result = ingest_thread_messages(mock_zep, msgs)

        mock_zep.thread.add_messages.assert_not_called()  # no double ingestion
        assert result.batch_ids == [BATCH_UUID]
        assert result.add_errors[-1].index == -1
        assert "result.batch_ids" in result.add_errors[-1].error

    def test_sequential_groups_by_thread(self, mock_zep):
        # the sequential path issues one add_messages call per thread, preserving
        # per-thread order
        msgs = [
            message(content="first"),
            message(thread_uuid=OTHER_THREAD_UUID, content="other thread"),
            message(content="second", role="assistant"),
        ]
        result = ingest_thread_messages(mock_zep, msgs, method="sequential")
        assert result.method == "sequential"
        assert result.items_submitted == 3
        calls = {
            c.args[0]: c.kwargs["messages"] for c in mock_zep.thread.add_messages.call_args_list
        }
        assert [m.content for m in calls[THREAD_UUID]] == ["first", "second"]
        assert calls[THREAD_UUID][1].role == "assistant"
        assert [m.content for m in calls[OTHER_THREAD_UUID]] == ["other thread"]

    def test_explicit_batch_raises_when_endpoint_not_found(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=404)
        with pytest.raises(BatchUnavailableError):
            ingest_thread_messages(mock_zep, [message()], method="batch")

    def test_sequential_chunks_large_threads(self, mock_zep):
        msgs = [message(content=f"m{i}") for i in range(65)]
        ingest_thread_messages(mock_zep, msgs, method="sequential", messages_per_call=30)
        assert mock_zep.thread.add_messages.call_count == 3

    def test_messages_per_call_over_api_limit_rejected_before_any_call(self, mock_zep):
        # thread.add_messages caps a call at 30 messages; a larger chunk would
        # only be refused as a 400 after the first messages had been sent
        with pytest.raises(ConfigurationError, match="messages_per_call"):
            ingest_thread_messages(
                mock_zep,
                [message()],
                method="sequential",
                messages_per_call=MAX_MESSAGES_PER_THREAD_ADD + 1,
            )
        mock_zep.thread.add_messages.assert_not_called()

    def test_messages_per_call_at_api_limit_accepted(self, mock_zep):
        result = ingest_thread_messages(
            mock_zep,
            [message()],
            method="sequential",
            messages_per_call=MAX_MESSAGES_PER_THREAD_ADD,
        )
        assert result.items_submitted == 1

    def test_default_messages_per_call_chunks_at_the_api_limit(self, mock_zep):
        # the default is the same constant as the validated maximum, so the two
        # cannot drift apart — and the chunk size is the 30 thread.add_messages
        # limit, never the 350-item batch.add_items page size
        msgs = [message(content=f"m{i}") for i in range(MAX_MESSAGES_PER_THREAD_ADD + 1)]
        ingest_thread_messages(mock_zep, msgs, method="sequential")
        sizes = [len(c.kwargs["messages"]) for c in mock_zep.thread.add_messages.call_args_list]
        assert sizes == [MAX_MESSAGES_PER_THREAD_ADD, 1]

    def test_sequential_tracks_the_returned_task_per_thread(self, mock_zep):
        mock_zep.thread.add_messages.side_effect = [
            AddMessagesResult(task=make_task("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")),
            AddMessagesResult(task=make_task("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")),
        ]
        msgs = [message(), message(thread_uuid=OTHER_THREAD_UUID)]

        result = ingest_thread_messages(mock_zep, msgs, method="sequential")

        assert result.task_ids == [
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ]
        assert result.episode_uuids == []
        assert result.wait(poll_interval=0) is result
        assert result.status == "succeeded"

    def test_sequential_warns_when_response_has_no_task(self, mock_zep):
        mock_zep.thread.add_messages.return_value = AddMessagesResult()

        result = ingest_thread_messages(mock_zep, [message()], method="sequential")

        assert result.task_ids == []
        assert result.untracked_items == 1
        assert result.status == "untracked"
        assert any("no task" in warning for warning in result.warnings)
        with pytest.raises(IngestUntrackedError):
            result.wait()

    def test_sequential_failure_recorded_and_continues(self, mock_zep):
        mock_zep.thread.add_messages.side_effect = [
            ApiError(status_code=400, body="bad"),
            AddMessagesResult(task=make_task()),
        ]
        msgs = [message(), message(thread_uuid=OTHER_THREAD_UUID)]
        result = ingest_thread_messages(mock_zep, msgs, method="sequential")
        assert len(result.add_errors) == 1
        assert result.items_submitted == 1


class TestIgnoreRoles:
    def test_batch_passes_ignore_roles_to_create(self, mock_zep):
        ingest_thread_messages(
            mock_zep,
            [message()],
            method="batch",
            ignore_roles=["assistant"],
        )
        assert mock_zep.batch.create.call_args.kwargs["ignore_roles"] == ["assistant"]

    def test_sequential_passes_ignore_roles_to_add_messages(self, mock_zep):
        ingest_thread_messages(
            mock_zep,
            [message()],
            method="sequential",
            ignore_roles=["assistant"],
        )
        assert mock_zep.thread.add_messages.call_args.kwargs["ignore_roles"] == ["assistant"]

    def test_ignore_roles_omitted_when_unset(self, mock_zep):
        # unset -> the field is never sent, so the SDK applies its own default
        ingest_thread_messages(mock_zep, [message()], method="batch")
        assert "ignore_roles" not in mock_zep.batch.create.call_args.kwargs

    def test_duplicate_roles_are_deduplicated_in_order(self, mock_zep):
        ingest_thread_messages(
            mock_zep,
            [message()],
            method="batch",
            ignore_roles=["assistant", "assistant", "system"],
        )
        assert mock_zep.batch.create.call_args.kwargs["ignore_roles"] == ["assistant", "system"]

    def test_unknown_ignore_role_rejected_before_any_call(self, mock_zep):
        with pytest.raises(ConfigurationError, match="unknown role"):
            ingest_thread_messages(mock_zep, [message()], ignore_roles=["customer"])
        mock_zep.batch.create.assert_not_called()
        mock_zep.thread.add_messages.assert_not_called()

    def test_bare_string_ignore_roles_rejected(self, mock_zep):
        # a common mistake: ignore_roles="assistant" would iterate into characters
        with pytest.raises(ConfigurationError, match="bare string"):
            ingest_thread_messages(mock_zep, [message()], ignore_roles="assistant")


class TestFileSources:
    def test_jsonl_source(self, mock_zep, tmp_path):
        file = tmp_path / "chat.jsonl"
        rows = [
            {
                "thread_uuid": THREAD_UUID,
                "role": "user",
                "name": "Avery Brown",
                "content": "hello",
            },
            {
                "thread_uuid": THREAD_UUID,
                "role": "assistant",
                "name": "Riley Chen",
                "content": "hi Avery Brown",
            },
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        result = ingest_thread_messages(mock_zep, file)
        assert result.items_submitted == 2

    def test_json_array_source(self, mock_zep, tmp_path):
        file = tmp_path / "chat.json"
        rows = [
            {
                "thread_uuid": THREAD_UUID,
                "role": "user",
                "name": "Avery Brown",
                "content": "hello",
            },
            {
                "thread_uuid": THREAD_UUID,
                "role": "assistant",
                "name": "Riley Chen",
                "content": "hi",
            },
        ]
        file.write_text(json.dumps(rows, indent=2))
        result = ingest_thread_messages(mock_zep, file)
        assert result.items_submitted == 2

    def test_invalid_row_raises_before_any_call(self, mock_zep, tmp_path):
        file = tmp_path / "chat.jsonl"
        file.write_text(
            json.dumps(
                {
                    "thread_uuid": THREAD_UUID,
                    "role": "nope",
                    "name": "Avery Brown",
                    "content": "x",
                }
            )
        )
        with pytest.raises(ConfigurationError):
            ingest_thread_messages(mock_zep, file)
        mock_zep.batch.add_items.assert_not_called()
        mock_zep.thread.add_messages.assert_not_called()

    def test_row_missing_required_field_names_the_field_and_row(self, mock_zep, tmp_path):
        # real chat exports routinely omit name; an omitted column is a
        # ConfigurationError pointing at the row, not a dataclass TypeError
        file = tmp_path / "chat.jsonl"
        rows = [
            {
                "thread_uuid": THREAD_UUID,
                "role": "user",
                "name": "Avery Brown",
                "content": "hello",
            },
            {
                "thread_uuid": THREAD_UUID,
                "role": "user",
                "content": "hi",
            },
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        with pytest.raises(ConfigurationError, match=r"Row 1 is missing required field\(s\): name"):
            ingest_thread_messages(mock_zep, file)
        mock_zep.batch.add_items.assert_not_called()
        mock_zep.thread.add_messages.assert_not_called()

    def test_empty_role_in_file_is_rejected(self, mock_zep, tmp_path):
        file = tmp_path / "chat.jsonl"
        file.write_text(
            json.dumps(
                {
                    "thread_uuid": THREAD_UUID,
                    "role": "",
                    "name": "Avery Brown",
                    "content": "hello",
                }
            )
        )
        with pytest.raises(ConfigurationError, match="role"):
            ingest_thread_messages(mock_zep, file)
        mock_zep.batch.add_items.assert_not_called()
