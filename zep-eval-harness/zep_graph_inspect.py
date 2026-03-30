"""
Zep Graph Inspection Script
Retrieve and print entities, edges, and/or episodes for one or more user or standalone graphs.
"""

import os
import argparse
import asyncio

from dotenv import load_dotenv
from zep_cloud.client import AsyncZep

VALID_INCLUDES = {"entities", "edges", "episodes"}
DEFAULT_INCLUDES = ["entities", "edges"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect Zep knowledge graphs — print entities, edges, and/or episodes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_graph_inspect.py --user USER_ID_1
  uv run zep_graph_inspect.py --graph GRAPH_ID_1
  uv run zep_graph_inspect.py --graph GRAPH_ID_1 --include entities edges episodes
  uv run zep_graph_inspect.py --graph GRAPH_ID_1 --include episodes
  uv run zep_graph_inspect.py --user USER_ID_1 --include edges
""",
    )
    parser.add_argument(
        "--user",
        nargs="+",
        type=str,
        help="One or more Zep user IDs (the full ID with random suffix, e.g. from manifest.json)",
    )
    parser.add_argument(
        "--graph",
        nargs="+",
        type=str,
        help="One or more standalone graph IDs (e.g. from manifest.json documents.graph_id)",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        type=str,
        default=DEFAULT_INCLUDES,
        choices=sorted(VALID_INCLUDES),
        help="What to include in the output (default: entities edges)",
    )
    args = parser.parse_args()
    if not args.user and not args.graph:
        parser.error("At least one of --user or --graph is required")
    return args


def print_nodes(nodes):
    """Print nodes in a readable format."""
    if not nodes:
        print("  (none)")
        return

    for i, node in enumerate(nodes, 1):
        print(f"  [{i}] {node.name}")
        if getattr(node, "label", None):
            print(f"      Label: {node.label}")
        if getattr(node, "summary", None):
            print(f"      Summary: {node.summary}")
        if getattr(node, "uuid_", None):
            print(f"      UUID: {node.uuid_}")
        print()


def print_edges(edges):
    """Print edges in a readable format."""
    if not edges:
        print("  (none)")
        return

    for i, edge in enumerate(edges, 1):
        fact = getattr(edge, "fact", None) or "(no fact)"
        print(f"  [{i}] {fact}")
        if getattr(edge, "name", None):
            print(f"      Name: {edge.name}")
        valid_at = getattr(edge, "valid_at", None)
        invalid_at = getattr(edge, "invalid_at", None)
        print(f"      Valid at:   {valid_at or '(not set)'}")
        print(f"      Invalid at: {invalid_at or '(not set)'}")
        if getattr(edge, "uuid_", None):
            print(f"      UUID: {edge.uuid_}")
        print()


def print_episodes(episodes):
    """Print episodes in a readable format."""
    if not episodes:
        print("  (none)")
        return

    for i, ep in enumerate(episodes, 1):
        content = getattr(ep, "content", "") or ""
        # Show first 200 chars of content as preview, full content indented below
        preview = content[:200].replace("\n", " ")
        if len(content) > 200:
            preview += "..."
        print(f"  [{i}] {preview}")
        if getattr(ep, "source", None):
            print(f"      Source: {ep.source}")
        if getattr(ep, "source_description", None):
            print(f"      Source desc: {ep.source_description}")
        processed = getattr(ep, "processed", None)
        if processed is not None:
            print(f"      Processed: {processed}")
        if getattr(ep, "created_at", None):
            print(f"      Created at: {ep.created_at}")
        if getattr(ep, "uuid_", None):
            print(f"      UUID: {ep.uuid_}")
        # Print full content indented
        if content:
            print(f"      --- Full content ---")
            for line in content.splitlines():
                print(f"      {line}")
        print()


async def inspect_user_graph(zep_client: AsyncZep, user_id: str, includes: set):
    """Fetch and print entities/edges/episodes for a user graph."""
    print(f"\nGraph type: User")
    print(f"User ID:    {user_id}")
    print()

    if "entities" in includes:
        nodes = await zep_client.graph.node.get_by_user_id(user_id=user_id)
        print(f"ENTITIES ({len(nodes) if nodes else 0})")
        print("-" * 60)
        print_nodes(nodes)

    if "edges" in includes:
        edges = await zep_client.graph.edge.get_by_user_id(user_id=user_id)
        print(f"EDGES ({len(edges) if edges else 0})")
        print("-" * 60)
        print_edges(edges)

    if "episodes" in includes:
        result = await zep_client.graph.episode.get_by_user_id(user_id=user_id, lastn=1000)
        episodes = result.episodes if result and result.episodes else []
        print(f"EPISODES ({len(episodes)})")
        print("-" * 60)
        print_episodes(episodes)


async def inspect_standalone_graph(zep_client: AsyncZep, graph_id: str, includes: set):
    """Fetch and print entities/edges/episodes for a standalone graph."""
    print(f"\nGraph type: Standalone")
    print(f"Graph ID:   {graph_id}")
    print()

    if "entities" in includes:
        nodes = await zep_client.graph.node.get_by_graph_id(graph_id=graph_id)
        print(f"ENTITIES ({len(nodes) if nodes else 0})")
        print("-" * 60)
        print_nodes(nodes)

    if "edges" in includes:
        edges = await zep_client.graph.edge.get_by_graph_id(graph_id=graph_id)
        print(f"EDGES ({len(edges) if edges else 0})")
        print("-" * 60)
        print_edges(edges)

    if "episodes" in includes:
        result = await zep_client.graph.episode.get_by_graph_id(graph_id=graph_id, lastn=1000)
        episodes = result.episodes if result and result.episodes else []
        print(f"EPISODES ({len(episodes)})")
        print("-" * 60)
        print_episodes(episodes)


async def main():
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        exit(1)

    zep_client = AsyncZep(api_key=api_key)
    includes = set(args.include)

    print("=" * 60)
    print("ZEP GRAPH INSPECTION")
    print(f"Showing: {', '.join(sorted(includes))}")
    print("=" * 60)

    if args.user:
        for user_id in args.user:
            await inspect_user_graph(zep_client, user_id, includes)

    if args.graph:
        for graph_id in args.graph:
            await inspect_standalone_graph(zep_client, graph_id, includes)


if __name__ == "__main__":
    asyncio.run(main())
