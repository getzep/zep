"""Live smoke tests for zep-ingest against a real Zep account.

Requires ``ZEP_API_KEY`` (and ``ZEP_API_URL`` for zep-cloud). Runnable locally:

    keybank run zep-prod -- env ZEP_API_URL=https://api.getzep.com \\
      uv run --directory ingestion python scripts/smoke.py

Cases run in parallel where they share one throwaway graph. The document case
uses a single two-chunk ingestion to verify ``document_id`` grouping.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import Message

from zep_ingest import Episode, NodeItem, ingest, ingest_json_records, ingest_nodes
from zep_ingest._graph_api import get_document_episodes
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


def run_case(name: str, fn: Callable[[], str]) -> CaseResult:
    start = time.monotonic()
    try:
        detail = fn()
        return ok(name, time.monotonic() - start, detail)
    except Exception as exc:  # noqa: BLE001 — smoke script reports all failures
        return fail(name, time.monotonic() - start, f"{type(exc).__name__}: {exc}")


def build_cases(
    client: Zep, graph_id: str, user_id: str, run_id: str
) -> list[tuple[str, Callable[[], str]]]:
    stats_holder: list[PollStats] = []

    def case_document_two_chunks() -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handbook.txt"
            path.write_text(
                "Alice Chen joined Acme Corp as a product manager in January and owns the mobile "
                "roadmap for the entire platform team across all regions.\n\n"
                "In March she launched the Acme mobile app to enterprise customers and the launch "
                "exceeded adoption targets set for the first quarter of the fiscal year."
            )
            from zep_ingest.loaders.text import TextFileLoader
            from zep_ingest.transforms.chunker import TextChunker

            episodes = list(
                TextChunker(chunk_size=180, overlap=0).apply(TextFileLoader(path).load())
            )
            assert len(episodes) == 2
            document_id = episodes[0].document_id
            assert document_id and episodes[1].document_id == document_id
            t0 = time.monotonic()
            result = ingest(client, ListLoader(episodes), graph_id=graph_id, method="batch")
            submit_s = time.monotonic() - t0
            assert result.items_submitted == 2
            result.wait(poll_interval=5.0, timeout=600)
            assert result.batch_ids, "expected batch_ids for document ingest"
            items = client.batch.list_items(result.batch_ids[0], limit=10).items or []
            assert len(items) == 2, f"expected 2 batch items, got {len(items)}"
            assert all(getattr(item, "document_id", None) == document_id for item in items)
            listed = get_document_episodes(client, graph_id=graph_id, document_id=document_id)
            list_detail = (
                f"list_api={len(listed)}"
                if listed
                else "list_api=empty (document list may lag on some deployments)"
            )
            return (
                f"document_id={document_id!r} chunks=2 submit={submit_s:.1f}s "
                f"batch_items={len(items)} {list_detail} status={result.status}"
            )

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
        pre_wait_batch_calls = stats.batch_get
        t1 = time.monotonic()
        result.wait(poll_interval=5.0, timeout=600)
        wait_s = time.monotonic() - t1
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
        assert result.status == "succeeded"
        return (
            f"episodes={len(result.episode_uuids)} tail={tail[:8]}… "
            f"episode.get calls={sum(stats.episode_get.values()) - pre} status={result.status}"
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
        nodes.wait(poll_interval=5.0, timeout=300)
        assert nodes.status == "succeeded"
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
        return f"nodes.status={nodes.status} episodes.wait={ep_wait:.1f}s status={episodes.status}"

    def case_multi_file_one_wait() -> str:
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
            result = ingest_json_records(
                client,
                paths,
                graph_id=graph_id,
                id_field="id",
                created_at_field="date",
            )
            assert result.items_submitted == 3
            t1 = time.monotonic()
            result.wait(poll_interval=5.0, timeout=600)
            wait_s = time.monotonic() - t1
        return f"files=3 wait={wait_s:.1f}s status={result.status}"

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
        assert len(result.episode_uuids) == 2
        result.wait(poll_interval=5.0, timeout=600)
        assert result.status == "succeeded"
        return f"threads=2 status={result.status}"

    def case_thread_batch_backfill() -> str:
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
        result.wait(poll_interval=5.0, timeout=600)
        return f"messages=4 status={result.status}"

    return [
        ("document: two chunks share document_id", case_document_two_chunks),
        ("thread.add_messages has message_uuids not task_id", case_thread_add_messages_no_task_id),
        ("batch: submit 5 episodes then wait once", case_batch_submit_all_wait_once),
        ("sequential graph.add: tail episode poll", case_sequential_tail_episode_poll),
        ("phased: nodes.wait then episodes.wait", case_phased_nodes_then_episodes),
        ("multi-file json: one submit one wait", case_multi_file_one_wait),
        ("multi-thread sequential: poll each thread tail", case_multi_thread_sequential_wait),
        ("thread batch backfill: batch.get wait", case_thread_batch_backfill),
    ]


def main() -> int:
    run_id = uuid.uuid4().hex[:10]
    client = Zep()
    graph_id = f"ingest-smoke-{run_id}"
    user_id = f"ingest-smoke-user-{run_id}"
    client.graph.create(graph_id=graph_id, name=f"ingest smoke {run_id}")
    client.user.add(
        user_id=user_id,
        first_name="Ingest",
        last_name="Smoke",
        email=f"{user_id}@example.com",
    )

    cases = build_cases(client, graph_id, user_id, run_id)
    results: list[CaseResult] = []

    print(f"zep-ingest smoke  run_id={run_id}  graph_id={graph_id}")
    print("-" * 72)

    # Document case runs first (validates grouping); the rest run in parallel.
    first_name, first_fn = cases[0]
    results.append(run_case(first_name, first_fn))
    mark = "PASS" if results[0].ok else "FAIL"
    print(f"[{mark}] {results[0].name} ({results[0].seconds:.1f}s)")
    print(f"       {results[0].detail}")

    parallel_cases = cases[1:]
    with ThreadPoolExecutor(max_workers=len(parallel_cases)) as pool:
        futures = {pool.submit(run_case, name, fn): name for name, fn in parallel_cases}
        for future in as_completed(futures):
            result = future.result()
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
