"""Tests for ThreadMessage validation and ingest_thread_messages (user data)."""

import json
from types import SimpleNamespace

import pytest
from zep_cloud.core.api_error import ApiError
from zep_cloud.errors.not_found_error import NotFoundError

from tests.conftest import make_batch_summary
from zep_ingest.exceptions import BatchUnavailableError, ConfigurationError
from zep_ingest.threads import MAX_MESSAGE_CHARS, ThreadMessage, ingest_thread_messages


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("zep_ingest.submitters.sequential.time.sleep", lambda _: None)


def message(**overrides) -> ThreadMessage:
    kwargs = {
        "thread_id": "support-42",
        "role": "user",
        "content": "My dashboard isn't loading.",
        "name": "Avery Brown",
        "created_at": "2024-06-15T10:30:00Z",
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

    def test_empty_thread_id_raises(self):
        with pytest.raises(ConfigurationError, match="thread_id"):
            message(thread_id="")

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


class TestBatchPath:
    def test_batch_items_mapped_and_processed(self, mock_zep):
        result = ingest_thread_messages(
            mock_zep,
            [message(), message(role="assistant", name="Bot")],
            user_id="avery-brown",
            method="batch",
        )
        assert result.method == "batch"
        assert result.items_submitted == 2
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert all(i.type == "thread_message" for i in items)
        assert items[0].thread_id == "support-42"
        assert items[0].content == "My dashboard isn't loading."
        assert items[0].role == "user"
        assert items[0].name == "Avery Brown"
        assert items[0].created_at == "2024-06-15T10:30:00Z"
        mock_zep.batch.process.assert_called_once()

    def test_user_and_threads_ensured_before_submission(self, mock_zep):
        mock_zep.user.get.side_effect = NotFoundError(body=None)
        ingest_thread_messages(
            mock_zep,
            [message(), message(thread_id="support-43")],
            user_id="avery-brown",
        )
        mock_zep.user.add.assert_called_once_with(user_id="avery-brown")
        created = {c.kwargs["thread_id"] for c in mock_zep.thread.create.call_args_list}
        assert created == {"support-42", "support-43"}
        assert all(
            c.kwargs["user_id"] == "avery-brown" for c in mock_zep.thread.create.call_args_list
        )

    def test_sequential_sends_metadata(self, mock_zep):
        msgs = [message(metadata={"source": "zendesk"})]
        ingest_thread_messages(mock_zep, msgs, user_id="avery-brown", method="sequential")
        [sent] = mock_zep.thread.add_messages.call_args.kwargs["messages"]
        assert sent.metadata == {"source": "zendesk"}

    def test_thread_create_validation_error_raises(self, mock_zep):
        mock_zep.thread.create.side_effect = ApiError(
            status_code=400, body={"message": "bad request: invalid thread id"}
        )
        with pytest.raises(ApiError):
            ingest_thread_messages(mock_zep, [message()], user_id="avery-brown")

    def test_thread_id_suffix_applied(self, mock_zep):
        msgs = [message(created_at=None)]
        ingest_thread_messages(mock_zep, msgs, user_id="avery-brown", thread_id_suffix="-run7")
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert items[0].thread_id == "support-42-run7"
        mock_zep.thread.create.assert_called_once_with(
            thread_id="support-42-run7", user_id="avery-brown"
        )

    def test_no_sequential_fallback_after_partial_batch(self, mock_zep, monkeypatch):
        monkeypatch.setattr("zep_ingest.threads.MAX_ITEMS_PER_ADD", 1)
        monkeypatch.setattr("zep_ingest.threads.MAX_ITEMS_PER_BATCH", 1)
        mock_zep.batch.create.side_effect = [
            make_batch_summary("b1", "draft"),
            ApiError(status_code=403),
        ]
        msgs = [message(content=f"m{i}", created_at=None) for i in range(2)]
        with pytest.raises(BatchUnavailableError):
            ingest_thread_messages(mock_zep, msgs, user_id="avery-brown")
        mock_zep.thread.add_messages.assert_not_called()  # no double ingestion

    def test_existing_thread_conflict_tolerated(self, mock_zep):
        mock_zep.thread.create.side_effect = ApiError(status_code=409, body="exists")
        mock_zep.thread.get.return_value = SimpleNamespace(user_id="avery-brown")
        result = ingest_thread_messages(mock_zep, [message()], user_id="avery-brown")
        assert result.items_submitted == 1

    def test_existing_thread_400_already_exists_tolerated(self, mock_zep):
        # the live API reports duplicates as 400 "... already exists"
        mock_zep.thread.create.side_effect = ApiError(
            status_code=400, body={"message": "bad request: session with id x already exists"}
        )
        mock_zep.thread.get.return_value = SimpleNamespace(user_id="avery-brown")
        result = ingest_thread_messages(mock_zep, [message()], user_id="avery-brown")
        assert result.items_submitted == 1

    def test_existing_thread_for_another_user_is_rejected(self, mock_zep):
        mock_zep.thread.create.side_effect = ApiError(status_code=409, body="exists")
        mock_zep.thread.get.return_value = SimpleNamespace(user_id="another-user")
        with pytest.raises(ConfigurationError, match="already belongs"):
            ingest_thread_messages(mock_zep, [message()], user_id="avery-brown")
        mock_zep.thread.add_messages.assert_not_called()

    def test_user_id_required(self, mock_zep):
        with pytest.raises(ConfigurationError, match="user_id"):
            ingest_thread_messages(mock_zep, [message()])

    def test_oversize_content_split_with_warning(self, mock_zep):
        long = "A perfectly normal sentence. " * 300  # ~8700 chars
        result = ingest_thread_messages(
            mock_zep, [message(content=long)], user_id="avery-brown", method="batch"
        )
        items = mock_zep.batch.add.call_args.kwargs["items"]
        assert len(items) > 1
        assert all(len(i.content) <= MAX_MESSAGE_CHARS for i in items)
        assert any("split" in w.lower() for w in result.warnings)

    def test_missing_created_at_warns(self, mock_zep):
        result = ingest_thread_messages(mock_zep, [message(created_at=None)], user_id="avery-brown")
        assert any("created_at" in w for w in result.warnings)

    def test_process_failure_recorded_not_raised(self, mock_zep):
        mock_zep.batch.process.side_effect = ApiError(status_code=500, body="boom")
        result = ingest_thread_messages(
            mock_zep, [message(created_at=None)], user_id="avery-brown", method="batch"
        )
        assert result.items_submitted == 1
        [error] = result.add_errors
        assert error.batch_id == "batch-1"
        assert "process" in error.error
        assert result.status == "failed"


class TestSequentialPath:
    def test_auto_prefers_sequential_when_timestamps_present(self, mock_zep):
        # the Batch API currently drops created_at on thread_message items,
        # which silently corrupts backfill timelines — auto must protect that
        msgs = [
            ThreadMessage(
                thread_id="t1", role="user", content="hi", created_at="2024-06-15T10:30:00Z"
            )
        ]
        result = ingest_thread_messages(mock_zep, msgs, user_id="u1")
        assert result.method == "sequential"
        mock_zep.batch.create.assert_not_called()
        assert any("created_at" in w for w in result.warnings)

    def test_auto_uses_batch_when_no_timestamps(self, mock_zep):
        msgs = [ThreadMessage(thread_id="t1", role="user", content="hi")]
        result = ingest_thread_messages(mock_zep, msgs, user_id="u1")
        assert result.method == "batch"

    def test_explicit_batch_with_timestamps_warns(self, mock_zep):
        msgs = [
            ThreadMessage(
                thread_id="t1", role="user", content="hi", created_at="2024-06-15T10:30:00Z"
            )
        ]
        result = ingest_thread_messages(mock_zep, msgs, user_id="u1", method="batch")
        assert result.method == "batch"
        assert any("created_at" in w for w in result.warnings)

    def test_auto_falls_back_and_groups_by_thread(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=403)
        msgs = [  # no created_at: auto tries batch first, then hits the gate
            message(content="first", created_at=None),
            message(thread_id="support-43", content="other thread", created_at=None),
            message(content="second", role="assistant", created_at=None),
        ]
        result = ingest_thread_messages(mock_zep, msgs, user_id="avery-brown")
        assert result.method == "sequential"
        assert result.items_submitted == 3
        calls = {
            c.args[0]: c.kwargs["messages"] for c in mock_zep.thread.add_messages.call_args_list
        }
        assert [m.content for m in calls["support-42"]] == ["first", "second"]
        assert calls["support-42"][1].role == "assistant"
        assert [m.content for m in calls["support-43"]] == ["other thread"]

    def test_explicit_batch_raises_on_gating(self, mock_zep):
        mock_zep.batch.create.side_effect = ApiError(status_code=403)
        with pytest.raises(BatchUnavailableError):
            ingest_thread_messages(mock_zep, [message()], user_id="avery-brown", method="batch")

    def test_sequential_chunks_large_threads(self, mock_zep):
        msgs = [message(content=f"m{i}") for i in range(65)]
        ingest_thread_messages(
            mock_zep, msgs, user_id="avery-brown", method="sequential", messages_per_call=30
        )
        assert mock_zep.thread.add_messages.call_count == 3

    def test_sequential_failure_recorded_and_continues(self, mock_zep):
        mock_zep.thread.add_messages.side_effect = [ApiError(status_code=400, body="bad"), None]
        msgs = [message(), message(thread_id="support-43")]
        result = ingest_thread_messages(mock_zep, msgs, user_id="avery-brown", method="sequential")
        assert len(result.add_errors) == 1
        assert result.items_submitted == 1


class TestFileSources:
    def test_jsonl_source(self, mock_zep, tmp_path):
        file = tmp_path / "chat.jsonl"
        rows = [
            {
                "thread_id": "t1",
                "role": "user",
                "name": "Avery Brown",
                "content": "hello",
                "created_at": "2024-06-15T10:30:00Z",
            },
            {"thread_id": "t1", "role": "assistant", "content": "hi Avery Brown"},
        ]
        file.write_text("\n".join(json.dumps(r) for r in rows))
        result = ingest_thread_messages(mock_zep, file, user_id="avery-brown")
        assert result.items_submitted == 2

    def test_json_array_source(self, mock_zep, tmp_path):
        file = tmp_path / "chat.json"
        rows = [
            {
                "thread_id": "t1",
                "role": "user",
                "content": "hello",
                "created_at": "2024-06-15T10:30:00Z",
            },
            {"thread_id": "t1", "role": "assistant", "content": "hi"},
        ]
        file.write_text(json.dumps(rows, indent=2))
        result = ingest_thread_messages(mock_zep, file, user_id="avery-brown")
        assert result.items_submitted == 2

    def test_invalid_row_raises_before_any_call(self, mock_zep, tmp_path):
        file = tmp_path / "chat.jsonl"
        file.write_text(json.dumps({"thread_id": "t1", "role": "nope", "content": "x"}))
        with pytest.raises(ConfigurationError):
            ingest_thread_messages(mock_zep, file, user_id="avery-brown")
        mock_zep.batch.add.assert_not_called()
        mock_zep.thread.add_messages.assert_not_called()
