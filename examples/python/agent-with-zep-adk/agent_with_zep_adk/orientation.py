"""Graph orientation: learn the graph once, then reuse the result.

Orientation reads the ontology (defined in ontology.py) and samples the most
connected nodes by degree. The result is cached per graph id so agent runs do
not repeat the queries. Run `python -m agent_with_zep_adk.orientation --refresh`
to rebuild the cache after re-ingesting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from zep_cloud import AsyncZep

from . import ontology
from .config import CACHE_DIR, ORIENTATION_SAMPLE_SIZE, Settings


async def fetch_orientation(zep: AsyncZep, graph_id: str) -> dict:
    """Query the graph for the orientation payload."""
    nodes = await zep.graph.node.get_by_graph_id(
        graph_id, order_by="degree", limit=ORIENTATION_SAMPLE_SIZE
    )
    return {
        "graph_id": graph_id,
        "entity_types": ontology.render_entity_types(),
        "edge_types": ontology.render_edge_types(),
        "nodes": [{"uuid": n.uuid_, "name": n.name, "labels": n.labels or []} for n in nodes or []],
    }


def cache_path(graph_id: str) -> Path:
    return CACHE_DIR / f"orientation-{graph_id}.json"


async def load_orientation(zep: AsyncZep, graph_id: str, refresh: bool = False) -> dict:
    """Return the cached orientation, or fetch and cache it."""
    path = cache_path(graph_id)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    data = await fetch_orientation(zep, graph_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch or refresh the graph orientation cache.")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-fetch")
    args = parser.parse_args()
    settings = Settings.from_env()
    zep = AsyncZep(
        api_key=settings.zep_api_key,
        **({"base_url": settings.zep_base_url} if settings.zep_base_url else {}),
    )
    data = asyncio.run(load_orientation(zep, settings.graph_id, refresh=args.refresh))
    print(f"orientation cached at {cache_path(settings.graph_id)} ({len(data['nodes'])} nodes)")


if __name__ == "__main__":
    main()
