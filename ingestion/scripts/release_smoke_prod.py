"""Production smoke tests for zep-ingest 0.3.0 release candidates.

Exercises submit-all-then-wait-once, tail polling, thread message UUIDs,
multi-thread sagas, and phased node seeding. Requires ZEP_API_KEY (and
ZEP_API_URL for zep-cloud). Run via KeyBank:

    keybank run zep-prod -- env ZEP_API_URL=https://api.getzep.com \\
      uv run --directory ingestion python scripts/release_smoke_prod.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import Message

from zep_ingest import Episode, NodeItem, ingest, ingest_json_records, ingest_nodes
from zep_ingest.exceptions import BatchUnavailableError
from zep_ingest.threads import ThreadMessage, ingest_thread_messages


@dataclass
class PollStats:
    batch_get: int = 0
    episode_get: Counter[str] = field(default_factory=Counter)
    task_get: int = 0


def instrument_client(client: Zep) -> PollStats:
    stats = PollStats()
    orig_batch_get = client.batch.get
    orig_episode_get = client.graph.episode.get
    orig_task_get = client.task.get

    def batch_get(batch_id: str, *args: Any, **kwargs: Any):
        stats.batch_get += 1
        return orig_batch_get(batch_id, *args, **kwargs)

    def episode_get(*args: Any, **kwargs: Any):
        uuid_ = kwargs.get("uuid_") or (args[0] if args else "?")
        stats.episode_get[str(uuid_)] += 1
        return orig_episode_get(*args, **kwargs)

    def task_get(task_id: str, *args: Any, **kwargs: Any):
        stats.task_get += 1
        return orig_task_get(task_id, *args, **kwargs)

    client.batch.get = batch_get  # type: ignore[method-assign]
    client.graph.episode.get = episode_get  # type: ignore[method-assign]
    client.task.get = task_get  # type: ignore[method-assign]
    return stats


class ListLoader:
    def __init__(self, episodes: list[Episode]) -> None:
        self.episodes = episodes

    def load(self):
        yield from self.episodes


@dataclass
class CaseResult:
    name: str
    ok: bool
    seconds: float
    detail: str


def ok(name: str, seconds: float, detail: str) -> CaseResult:
    return CaseResult(name=name, ok=True, seconds=seconds, detail=detail)


def fail(name: str, seconds: float, detail: str) -> CaseResult:
    return CaseResult(name=name, ok=False, seconds=seconds, detail=detail)


def run_case(name: str, fn) -> CaseResult:
    start = time.monotonic()
    try:
        detail = fn()
        return ok(name, time.monotonic() - start, detail)
    except Exception as exc:  # noqa: BLE001 — smoke script reports all failures
        return fail(name, time.monotonic() - start, f"{type(exc).__name__}: {exc}")


def main() -> int:
    run_id = uuid.uuid4().hex[:10]
    client = Zep()
    stats_holder: list[PollStats] = []

    graph_id = f"ingest-smoke-{run_id}"
    user_id = f"ingest-smoke-user-{run_id}"
    client.graph.create(graph_id=graph_id, name=f"ingest smoke {run_id}")
    client.user.add(
        user_id=user_id,
        first_name="Ingest",
        last_name="Smoke",
        email=f"{user_id}@example.com",
    )

    results: list[CaseResult] = []

    def case_thread_add_messages_no_task_id() -> str:
        thread_id = f"probe-{run_id}"
        client.thread.create(thread_id=thread_id, user_id=user_id)
        response = client.thread.add_messages(
            thread_id,
            messages=[
                Message(
                    role="user",
                    name="Smoke Tester",
                    content="Probe: sequential thread.add_messages response shape.",
                    created_at="2025-06-01T12:00:00Z",
                )
            ],
        )
        uuids = getattr(response, "message_uuids", None) or []
        task_id = getattr(response, "task_id", None)
        if not uuids:
            raise AssertionError("expected message_uuids from thread.add_messages")
        if task_id is not None:
            raise AssertionError(f"expected task_id=None, got {task_id!r}")
        return f"message_uuids={len(uuids)}, task_id=null"

    def case_batch_submit_all_wait_once() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        episodes = [
            Episode(
                data=f"Batch smoke episode {i}: employee {run_id} shipped feature {i}.",
                created_at=f"2024-06-{10 + i:02d}T10:00:00Z",
            )
            for i in range(5)
        ]
        t0 = time.monotonic()
        try:
            result = ingest(client, ListLoader(episodes), graph_id=graph_id, method="batch")
        except BatchUnavailableError as exc:
            raise AssertionError("production must support batch API") from exc
        submit_s = time.monotonic() - t0
        assert result.items_submitted == 5
        assert result.batch_ids, "expected batch_ids"
        assert not result.episode_uuids
        pre_wait_batch_calls = stats.batch_get
        t1 = time.monotonic()
        result.wait(poll_interval=5.0, timeout=600)
        wait_s = time.monotonic() - t1
        assert result.status in ("succeeded", "partial")
        # Tail polling: should not call batch.get once per item per tick.
        wait_batch_calls = stats.batch_get - pre_wait_batch_calls
        return (
            f"submit={submit_s:.1f}s wait={wait_s:.1f}s batch_ids={len(result.batch_ids)} "
            f"batch.get during wait={wait_batch_calls} status={result.status}"
        )

    def case_sequential_tail_episode_poll() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        episodes = [
            Episode(
                data=f"Sequential smoke {i}: project {run_id} milestone {i}.",
                created_at=f"2024-07-{10 + i:02d}T09:00:00Z",
            )
            for i in range(3)
        ]
        result = ingest(client, ListLoader(episodes), graph_id=graph_id, method="sequential")
        assert len(result.episode_uuids) == 3
        tail = result.episode_uuids[-1]
        pre = sum(stats.episode_get.values())
        result.wait(poll_interval=5.0, timeout=300)
        polled_uuids = set(stats.episode_get.keys())
        assert result.status == "succeeded"
        # Should poll mostly the tail until it completes.
        assert tail in polled_uuids
        assert stats.episode_get[tail] >= 1
        assert stats.task_get == 0
        return (
            f"episodes={len(result.episode_uuids)} tail={tail[:8]}… "
            f"episode.get calls={sum(stats.episode_get.values()) - pre} "
            f"unique_uuids_polled={len(polled_uuids)} status={result.status}"
        )

    def case_phased_nodes_then_episodes() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        nodes = ingest_nodes(
            client,
            [
                NodeItem(name=f"Smoke Entity A {run_id}", label="Entity"),
                NodeItem(name=f"Smoke Entity B {run_id}", label="Entity"),
            ],
            graph_id=graph_id,
        )
        assert nodes.task_ids, "add_nodes should return task_ids"
        assert not nodes.episode_uuids
        t0 = time.monotonic()
        nodes.wait(poll_interval=5.0, timeout=300)
        nodes_wait = time.monotonic() - t0
        assert nodes.status == "succeeded"
        assert all(nodes.node_uuids)
        assert stats.task_get >= len(nodes.task_ids)
        pre_tasks = stats.task_get

        episodes = ingest(
            client,
            ListLoader(
                [
                    Episode(
                        data=f"After seeding nodes, {run_id} closed the release checklist.",
                        created_at="2024-08-01T12:00:00Z",
                    )
                ]
            ),
            graph_id=graph_id,
            method="batch",
        )
        t1 = time.monotonic()
        episodes.wait(poll_interval=5.0, timeout=300)
        ep_wait = time.monotonic() - t1
        assert episodes.status in ("succeeded", "partial")
        return (
            f"nodes.wait={nodes_wait:.1f}s node_uuids={nodes.node_uuids} "
            f"episodes.wait={ep_wait:.1f}s tasks_during_ep_wait={stats.task_get - pre_tasks} "
            f"status={episodes.status}"
        )

    def case_multi_file_one_wait() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                path = Path(tmp) / f"records_{i}.jsonl"
                path.write_text(
                    json.dumps(
                        {
                            "id": f"{run_id}-{i}",
                            "summary": f"JSONL file {i} for {run_id}: inventory update {i}.",
                            "date": f"2024-09-{10 + i:02d}T00:00:00Z",
                        }
                    )
                    + "\n"
                )
                paths.append(path)
            t0 = time.monotonic()
            result = ingest_json_records(
                client,
                paths,
                graph_id=graph_id,
                id_field="id",
                created_at_field="date",
            )
            submit_s = time.monotonic() - t0
            assert result.items_submitted == 3
            t1 = time.monotonic()
            result.wait(poll_interval=5.0, timeout=600)
            wait_s = time.monotonic() - t1
        assert result.status in ("succeeded", "partial")
        return (
            f"files=3 submit={submit_s:.1f}s wait={wait_s:.1f}s "
            f"batch.get={stats.batch_get} status={result.status}"
        )

    def case_multi_thread_sequential_wait() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        t1 = f"thread-a-{run_id}"
        t2 = f"thread-b-{run_id}"
        client.thread.create(thread_id=t1, user_id=user_id)
        client.thread.create(thread_id=t2, user_id=user_id)
        messages = [
            ThreadMessage(
                thread_id=t1,
                role="user",
                name="Alice",
                content=f"Thread A message 1 for {run_id}.",
                created_at="2025-01-01T10:00:00Z",
            ),
            ThreadMessage(
                thread_id=t2,
                role="user",
                name="Bob",
                content=f"Thread B message 1 for {run_id}.",
                created_at="2025-01-01T10:01:00Z",
            ),
            ThreadMessage(
                thread_id=t1,
                role="user",
                name="Alice",
                content=f"Thread A message 2 for {run_id}.",
                created_at="2025-01-01T10:02:00Z",
            ),
        ]
        result = ingest_thread_messages(client, messages, user_id=user_id, method="sequential")
        assert result.task_ids == []
        assert len(result.episode_uuids) == 2
        assert result._single_queue_episode_poll is False
        result.wait(poll_interval=5.0, timeout=600)
        assert result.status == "succeeded"
        polled = set(stats.episode_get.keys())
        assert polled == set(result.episode_uuids), f"polled {polled} vs {result.episode_uuids}"
        return (
            f"threads=2 poll_uuids={result.episode_uuids} "
            f"episode.get total={sum(stats.episode_get.values())} status={result.status}"
        )

    def case_thread_batch_backfill() -> str:
        stats = instrument_client(client)
        stats_holder.append(stats)
        thread_id = f"batch-thread-{run_id}"
        client.thread.create(thread_id=thread_id, user_id=user_id)
        messages = [
            ThreadMessage(
                thread_id=thread_id,
                role="user",
                name="Carol",
                content=f"Batch thread message {i} for {run_id}.",
                created_at=f"2025-02-{10 + i:02d}T12:00:00Z",
            )
            for i in range(4)
        ]
        try:
            result = ingest_thread_messages(client, messages, user_id=user_id, method="batch")
        except BatchUnavailableError as exc:
            raise AssertionError("production must support batch for thread backfill") from exc
        assert result.batch_ids
        assert not result.episode_uuids
        result.wait(poll_interval=5.0, timeout=600)
        assert result.status in ("succeeded", "partial")
        return (
            f"messages=4 batch_ids={len(result.batch_ids)} "
            f"batch.get={stats.batch_get} status={result.status}"
        )

    cases = [
        ("thread.add_messages has message_uuids not task_id", case_thread_add_messages_no_task_id),
        ("batch: submit 5 episodes then wait once", case_batch_submit_all_wait_once),
        ("sequential graph.add: tail episode poll", case_sequential_tail_episode_poll),
        ("phased: nodes.wait then episodes.wait", case_phased_nodes_then_episodes),
        ("multi-file json: one submit one wait", case_multi_file_one_wait),
        ("multi-thread sequential: poll each thread tail", case_multi_thread_sequential_wait),
        ("thread batch backfill: batch.get wait", case_thread_batch_backfill),
    ]

    print(f"zep-ingest production smoke  run_id={run_id}  graph_id={graph_id}")
    print("-" * 72)
    for name, fn in cases:
        result = run_case(name, fn)
        results.append(result)
        mark = "PASS" if result.ok else "FAIL"
        print(f"[{mark}] {result.name} ({result.seconds:.1f}s)")
        print(f"       {result.detail}")

    print("-" * 72)
    passed = sum(1 for r in results if r.ok)
    print(f"{passed}/{len(results)} passed")

    try:
        client.graph.delete(graph_id)
    except Exception:  # noqa: BLE001
        print(f"warning: could not delete graph {graph_id}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
