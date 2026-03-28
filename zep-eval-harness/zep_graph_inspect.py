"""
Zep Graph Inspection Script
Retrieve and print all nodes (entities) and edges (facts) for one or more user or standalone graphs.
"""

import os
import argparse
import asyncio

from dotenv import load_dotenv
from zep_cloud.client import AsyncZep


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect Zep knowledge graphs — print all entities and edges.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_graph_inspect.py --user USER_ID_1
  uv run zep_graph_inspect.py --user USER_ID_1 USER_ID_2
  uv run zep_graph_inspect.py --graph GRAPH_ID_1 GRAPH_ID_2
  uv run zep_graph_inspect.py --user USER_ID_1 --graph GRAPH_ID_1
  uv run zep_graph_inspect.py --user USER_ID_1 --edges-only
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
        "--nodes-only",
        action="store_true",
        help="Only print nodes (entities)",
    )
    parser.add_argument(
        "--edges-only",
        action="store_true",
        help="Only print edges (facts)",
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


async def inspect_user_graph(zep_client: AsyncZep, user_id: str, args):
    """Fetch and print nodes/edges for a user graph."""
    print(f"\nGraph type: User")
    print(f"User ID:    {user_id}")
    print()

    show_nodes = not args.edges_only
    show_edges = not args.nodes_only

    if show_nodes:
        nodes = await zep_client.graph.node.get_by_user_id(user_id=user_id)
        print(f"NODES ({len(nodes) if nodes else 0})")
        print("-" * 60)
        print_nodes(nodes)

    if show_edges:
        edges = await zep_client.graph.edge.get_by_user_id(user_id=user_id)
        print(f"EDGES ({len(edges) if edges else 0})")
        print("-" * 60)
        print_edges(edges)


async def inspect_standalone_graph(zep_client: AsyncZep, graph_id: str, args):
    """Fetch and print nodes/edges for a standalone graph."""
    print(f"\nGraph type: Standalone")
    print(f"Graph ID:   {graph_id}")
    print()

    show_nodes = not args.edges_only
    show_edges = not args.nodes_only

    if show_nodes:
        nodes = await zep_client.graph.node.get_by_graph_id(graph_id=graph_id)
        print(f"NODES ({len(nodes) if nodes else 0})")
        print("-" * 60)
        print_nodes(nodes)

    if show_edges:
        edges = await zep_client.graph.edge.get_by_graph_id(graph_id=graph_id)
        print(f"EDGES ({len(edges) if edges else 0})")
        print("-" * 60)
        print_edges(edges)


async def main():
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        exit(1)

    zep_client = AsyncZep(api_key=api_key)

    print("=" * 60)
    print("ZEP GRAPH INSPECTION")
    print("=" * 60)

    if args.user:
        for user_id in args.user:
            await inspect_user_graph(zep_client, user_id, args)

    if args.graph:
        for graph_id in args.graph:
            await inspect_standalone_graph(zep_client, graph_id, args)


if __name__ == "__main__":
    asyncio.run(main())
