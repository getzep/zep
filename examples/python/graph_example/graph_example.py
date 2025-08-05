"""
Example of using the Zep Python SDK asynchronously with Graph functionality.

This script demonstrates the following functionality:
- Creating a graph.
- Updating a graph.
- Adding episodes to the graph (text and JSON).
- Retrieving nodes from the graph.
- Retrieving edges from the graph.
- Searching the graph for specific content.

The script showcases various operations using the Zep Graph API, including
graph management, adding different types of episodes, and querying the graph structure.
"""

import asyncio
import os
import uuid

from dotenv import find_dotenv, load_dotenv

from zep_cloud.client import AsyncZep

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )

    graph_id = f"slack:{uuid.uuid4().hex}"
    print(f"Creating graph {graph_id}...")
    graph = await client.graph.create(
        graph_id=graph_id,
        name="My Graph",
        description="This is my graph",
    )
    await asyncio.sleep(2)
    print(f"graph {graph_id} created {graph}")

    print(f"Adding episode to graph {graph_id}...")
    await client.graph.add(
        graph_id=graph_id,
        data="This is a test episode",
        type="text",
    )
    await asyncio.sleep(2)
    print(f"Adding more meaningful episode to graph {graph_id}...")
    await client.graph.add(
        graph_id=graph_id,
        data="Eric Clapton is a rock star",
        type="text",
    )
    await asyncio.sleep(2)
    print(f"Adding a JSON episode to graph {graph_id}...")
    json_string = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}'
    await client.graph.add(
        graph_id=graph_id,
        data=json_string,
        type="json",
    )
    await asyncio.sleep(20)

    # TODO: Need to enable non-message episodic content retrieval
    print(f"Getting episodes from graph {graph_id}...")
    results = await client.graph.episode.get_by_graph_id(graph_id, lastn=2)
    await asyncio.sleep(2)
    print(f"Episodes from graph {graph_id} {results.episodes}")
    episode = await client.graph.episode.get(results.episodes[0].uuid_)
    await asyncio.sleep(2)
    print(f"Episode {episode.uuid_} from graph {graph_id} {episode}")

    print(f"Getting nodes from graph {graph_id}...")
    nodes = await client.graph.node.get_by_graph_id(graph_id)
    await asyncio.sleep(2)
    print(f"Nodes from graph {graph_id} {nodes}")

    print(f"Getting edges from graph {graph_id}...")
    edges = await client.graph.edge.get_by_graph_id(graph_id)
    await asyncio.sleep(2)
    print(f"Edges from graph {graph_id} {edges}")

    print(f"Searching graph {graph_id}...")
    search_results = await client.graph.search(
        graph_id=graph_id,
        query="Eric Clapton",
    )
    await asyncio.sleep(2)
    print(f"Search results from graph {graph_id} {search_results}")

    # await client.graph.delete(graph_id)
    # print(f"graph {graph_id} deleted")


if __name__ == "__main__":
    asyncio.run(main())