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
import time

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
    print(f"Created graph UUID: {graph_uuid}")

    results = []
    print(f"Adding episodes to graph {graph_uuid}...")
    results.append(await client.graph.episode.add(graph_uuid, data="This is a test episode", type="text"))
    results.append(await client.graph.episode.add(graph_uuid, data="Eric Clapton is a rock star", type="text"))
    json_string = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}'
    results.append(await client.graph.episode.add(
        graph_uuid,
        data=json_string,
        type="json",
    ))
    task_uuid = results[-1].task.uuid_ if results[-1].task else None
    if task_uuid:
        deadline = time.monotonic() + 1200
        next_status_report = time.monotonic() + 30
        while True:
            task = await client.task.get(task_uuid)
            if task.status == "succeeded":
                break
            if task.status == "partial":
                print(f"Warning: task {task_uuid} ended with partial status: {task.error}")
                break
            if task.status == "failed":
                raise RuntimeError(f"Episode task ended with failed status: {task.error}")
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Timed out waiting for task {task_uuid}")
            if now >= next_status_report:
                print(f"Task {task_uuid} status={task.status}; still waiting.")
                next_status_report = now + 30
            await asyncio.sleep(2)

    print(f"Getting episodes from graph {graph_uuid}...")
    episodes = await client.graph.episode.list(graph_uuid, page_size=10)
    print(f"Episodes from graph {graph_uuid}: {episodes.items}")
    episode = await client.graph.episode.get(graph_uuid, results[-1].episode.uuid_)
    print(f"Episode {episode.uuid_} from graph {graph_uuid}: {episode}")

    print(f"Getting nodes from graph {graph_uuid}...")
    nodes = await client.graph.node.list(graph_uuid, page_size=10)
    print(f"Nodes from graph {graph_uuid}: {nodes.items}")

    print(f"Getting edges from graph {graph_uuid}...")
    edges = await client.graph.edge.list(graph_uuid, page_size=10)
    print(f"Edges from graph {graph_uuid}: {edges.items}")

    print(f"Searching graph {graph_uuid}...")
    search_results = await client.graph.search_edges(
        graph_uuid,
        query="Eric Clapton",
        page_size=10,
    )
    print(f"Search results from graph {graph_uuid}: {search_results.items}")


if __name__ == "__main__":
    asyncio.run(main())