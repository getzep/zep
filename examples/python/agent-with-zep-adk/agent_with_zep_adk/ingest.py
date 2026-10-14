"""Ingest the Pemberline Medical dataset into a Zep graph.

Creates the graph (or resets it with --reset), applies the ontology, adds one
JSON episode per record, then adds the reports as text episodes in date order
with their front-matter metadata and a reference_time equal to the report date.

Usage: uv run python -m agent_with_zep_adk.ingest [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter

import yaml
from zep_cloud import AsyncZep
from zep_cloud.core.api_error import ApiError

from . import ontology
from .config import DATA_DIR, Settings
from .orientation import cache_path

RECORD_FILES = [
    "sites",
    "suppliers",
    "components",
    "products",
    "regulatory_filings",
    "teams",
    "employees",
    "customers",
    "quality_issues",
]

POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 600.0


def load_reports() -> list[dict]:
    """Read data/reports/*.md into (metadata, body) pairs sorted by date."""
    reports = []
    for path in sorted((DATA_DIR / "reports").glob("R*.md")):
        text = path.read_text()
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front)
        reports.append({"meta": meta, "body": body.strip()})
    reports.sort(key=lambda r: r["meta"]["date"])
    return reports


def report_episode_kwargs(report: dict) -> dict:
    """Build the graph.episode.add kwargs for one report.

    yaml.safe_load parses `date:` as datetime.date; metadata values must be
    strings, numbers, or booleans, and reference_time must be RFC 3339.
    """
    meta = report["meta"]
    date = meta["date"]
    return {
        "type": "text",
        "data": report["body"],
        "reference_time": f"{date.isoformat()}T00:00:00Z",
        "metadata": {
            k: (str(v) if hasattr(v, "isoformat") else v) for k, v in meta.items() if v is not None
        },
        "source_description": f"Pemberline report {meta['report_id']}",
    }


async def wait_processed(zep: AsyncZep, graph_uuid: str, episode_uuid: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        episode = await zep.graph.episode.get(graph_uuid, episode_uuid)
        if episode.processed:
            return
        await asyncio.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"episode {episode_uuid} did not finish within {POLL_TIMEOUT_S}s; Zep can still finish it"
    )


async def wait_task(zep: AsyncZep, task_uuid: str):
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        task = await zep.task.get(task_uuid)
        if task.status in {"succeeded", "partial", "failed"}:
            if task.status != "succeeded":
                message = task.error.message if task.error and task.error.message else task.error
                raise RuntimeError(f"episode task {task_uuid} {task.status}: {message}")
            return task.status
        await asyncio.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"episode task {task_uuid} did not finish within {POLL_TIMEOUT_S}s; Zep can still finish it"
    )


async def wait_episode(zep: AsyncZep, graph_uuid: str, result) -> str:
    task = result.task
    if task and task.uuid_:
        return await wait_task(zep, task.uuid_)
    episode = result.episode
    await wait_processed(zep, graph_uuid, episode.uuid_)
    return "no_task_processed"


def report_graph_uuid(graph_uuid: str) -> None:
    print(f"Created graph UUID: {graph_uuid}")
    print(f"Set GRAPH_UUID={graph_uuid} in .env")


async def ingest(zep: AsyncZep, graph_uuid: str, reset: bool = False) -> dict[str, int]:
    if reset:
        if not graph_uuid:
            raise RuntimeError("GRAPH_UUID is empty. Set it before running ingest --reset.")
        try:
            await zep.graph.delete(graph_uuid)
        except ApiError as error:
            if error.status_code != 404:
                raise
        removed = cache_path(graph_uuid)
        removed.unlink(missing_ok=True)
        print(f"reset: removed orientation cache {removed}")
        graph = await zep.graph.create(name="Pemberline Medical demo")
        graph_uuid = graph.uuid_
        report_graph_uuid(graph_uuid)
    elif graph_uuid:
        try:
            await zep.graph.get(graph_uuid)
        except ApiError as error:
            if error.status_code == 404:
                raise RuntimeError(
                    f"GRAPH_UUID={graph_uuid} was not found. Clear GRAPH_UUID to create a graph, "
                    "or set it to an existing graph UUID."
                ) from error
            raise
    else:
        graph = await zep.graph.create(name="Pemberline Medical demo")
        graph_uuid = graph.uuid_
        report_graph_uuid(graph_uuid)

    if not graph_uuid:
        raise RuntimeError("Zep did not return a graph UUID when it created the graph.")

    await zep.graph.set_ontology(
        graph_uuid,
        entity_types=ontology.ENTITY_TYPE_DEFINITIONS,
        edge_types=ontology.EDGE_TYPE_DEFINITIONS,
    )

    episode_results = []

    for name in RECORD_FILES:
        records = json.loads((DATA_DIR / "records" / f"{name}.json").read_text())
        for record in records:
            result = await zep.graph.episode.add(
                graph_uuid,
                type="json",
                data=json.dumps({"record_type": name, **record}, ensure_ascii=False),
                source_description=f"Pemberline {name} record",
            )
            episode_results.append(result)

    for report in load_reports():
        result = await zep.graph.episode.add(
            graph_uuid,
            **report_episode_kwargs(report),
        )
        episode_results.append(result)

    print(f"added {len(episode_results)} episodes; waiting for processing")
    task_statuses = Counter()
    for result in episode_results:
        task_statuses[await wait_episode(zep, graph_uuid, result)] += 1
    print(f"task statuses: {dict(sorted(task_statuses.items()))}")
    return dict(task_statuses)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete and recreate the graph")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.reset and not settings.graph_uuid:
        raise RuntimeError("GRAPH_UUID is empty. Set it before running ingest --reset.")
    zep = AsyncZep(
        api_key=settings.zep_api_key,
        **({"base_url": settings.zep_base_url} if settings.zep_base_url else {}),
    )
    asyncio.run(ingest(zep, settings.graph_uuid, reset=args.reset))


if __name__ == "__main__":
    main()
