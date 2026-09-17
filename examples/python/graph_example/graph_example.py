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

    print("Creating graph...")
    graph = await client.graph.create(
        name="My Graph",
        description="This is my graph",
    )
    graph_uuid = graph.uuid_
    await asyncio.sleep(2)
    print(f"graph {graph_uuid} created {graph}")

    print(f"Adding episode to graph {graph_uuid}...")
    await client.graph.episode.add(
        graph_uuid,
        data="This is a test episode",
        type="text",
    )
    await asyncio.sleep(2)
    print(f"Adding more meaningful episode to graph {graph_uuid}...")
    await client.graph.episode.add(
        graph_uuid,
        data="Eric Clapton is a rock star",
        type="text",
    )
    await asyncio.sleep(2)
    print(f"Adding a JSON episode to graph {graph_uuid}...")
    json_string = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}'
    await client.graph.episode.add(
        graph_uuid,
        data=json_string,
        type="json",
    )
    await asyncio.sleep(20)

    print(f"Getting episodes from graph {graph_uuid}...")
    episodes = [
        episode async for episode in await client.graph.episode.list(graph_uuid, limit=2)
    ]
    await asyncio.sleep(2)
    print(f"Episodes from graph {graph_uuid} {episodes}")
    episode = await client.graph.episode.get(graph_uuid, episodes[0].uuid_)
    await asyncio.sleep(2)
    print(f"Episode {episode.uuid_} from graph {graph_uuid} {episode}")

    print(f"Getting nodes from graph {graph_uuid}...")
    nodes = [node async for node in await client.graph.node.list(graph_uuid)]
    await asyncio.sleep(2)
    print(f"Nodes from graph {graph_uuid} {nodes}")

    print(f"Getting edges from graph {graph_uuid}...")
    edges = [edge async for edge in await client.graph.edge.list(graph_uuid)]
    await asyncio.sleep(2)
    print(f"Edges from graph {graph_uuid} {edges}")

    print(f"Searching graph {graph_uuid}...")
    search_results = [
        edge
        async for edge in await client.graph.search_edges(
            graph_uuid,
            query="Eric Clapton",
        )
    ]
    await asyncio.sleep(2)
    print(f"Search results from graph {graph_uuid} {search_results}")

    # await client.graph.delete(graph_uuid)
    # print(f"graph {graph_uuid} deleted")


if __name__ == "__main__":
    asyncio.run(main())