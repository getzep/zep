"""Ingest the Pemberline Medical dataset into a Zep graph.

Creates the graph (or resets it with --reset), applies the ontology, adds one
JSON episode per record, then adds the reports as text episodes in date order
with their front-matter metadata and a created_at equal to the report date.

Usage: uv run python -m agent_with_zep_adk.ingest [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

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
POLL_TIMEOUT_S = 120.0


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
    """Build the graph.add kwargs for one report.

    yaml.safe_load parses `date:` as datetime.date; metadata values must be
    strings, numbers, or booleans, and created_at must be RFC 3339.
    """
    meta = report["meta"]
    date = meta["date"]
    return {
        "type": "text",
        "data": report["body"],
        "created_at": f"{date.isoformat()}T00:00:00Z",
        "metadata": {
            k: (str(v) if hasattr(v, "isoformat") else v) for k, v in meta.items() if v is not None
        },
        "source_description": f"Pemberline report {meta['report_id']}",
    }


async def wait_processed(zep: AsyncZep, uuid: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        episode = await zep.graph.episode.get(uuid)
        if episode.processed:
            return
        await asyncio.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"episode {uuid} was not processed within {POLL_TIMEOUT_S}s")


async def ingest(zep: AsyncZep, graph_id: str, reset: bool = False) -> None:
    if reset:
        try:
            await zep.graph.delete(graph_id)
        except ApiError as e:  # graph may not exist yet
            print(f"reset: graph delete skipped ({e.status_code})")
        else:
            # the cached orientation holds UUIDs from the deleted graph
            removed = cache_path(graph_id)
            removed.unlink(missing_ok=True)
            print(f"reset: removed orientation cache {removed}")
    try:
        await zep.graph.create(graph_id=graph_id, name="Pemberline Medical demo")
    except ApiError as e:  # already exists -> idempotent
        print(f"graph already exists, reusing it ({e.status_code})")

    await zep.graph.set_ontology(
        entities=ontology.ENTITY_TYPES,
        edges=ontology.EDGE_TYPES,
        graph_ids=[graph_id],
    )

    episode_uuids: list[str] = []

    for name in RECORD_FILES:
        records = json.loads((DATA_DIR / "records" / f"{name}.json").read_text())
        for record in records:
            episode = await zep.graph.add(
                graph_id=graph_id,
                type="json",
                data=json.dumps({"record_type": name, **record}, ensure_ascii=False),
                source_description=f"Pemberline {name} record",
            )
            episode_uuids.append(episode.uuid_)

    for report in load_reports():
        episode = await zep.graph.add(graph_id=graph_id, **report_episode_kwargs(report))
        episode_uuids.append(episode.uuid_)

    print(f"added {len(episode_uuids)} episodes; waiting for processing")
    for uuid in episode_uuids:
        await wait_processed(zep, uuid)
    print("done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete and recreate the graph")
    args = parser.parse_args()
    settings = Settings.from_env()
    zep = AsyncZep(
        api_key=settings.zep_api_key,
        **({"base_url": settings.zep_base_url} if settings.zep_base_url else {}),
    )
    asyncio.run(ingest(zep, settings.graph_id, reset=args.reset))


if __name__ == "__main__":
    main()
