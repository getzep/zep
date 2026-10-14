"""
Example of using the Zep Python SDK asynchronously with Graph functionality.

This script demonstrates the following functionality:
- Creating a user.
- Creating a thread associated with the created user.
- Adding messages to the thread.
- Retrieving episodes, edges, and nodes for a user.
- Searching the user's graph memory.
- Adding text and JSON episodes to the graph.
- Performing a centered search on a specific node.

The script showcases various operations using the Zep Graph API, including
user and thread management, adding different types of episodes, and querying
the graph structure.
"""

import asyncio
import os
import time
import uuid
import json
from dotenv import find_dotenv, load_dotenv
from conversations import history

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )
    user = await client.user.create(
        user_id=f"examples-v4-{uuid.uuid4().hex}",
        first_name="Paul",
    )
    user_uuid = user.uuid_
    print(f"Created user UUID: {user_uuid}")
    thread = await client.thread.create(user_uuid=user_uuid)
    thread_uuid = thread.uuid_
    print(f"Created thread UUID: {thread_uuid}")
    messages = [
        Message(role=message["role"], name=message["name"], content=message["content"])
        for message in history[2]
    ]
    added = await client.thread.add_messages(thread_uuid, messages=messages)
    if added.task and added.task.uuid_:
        deadline = time.monotonic() + 180
        while True:
            task = await client.task.get(added.task.uuid_)
            if task.status == "succeeded":
                break
            if task.status in {"partial", "failed"}:
                raise RuntimeError(f"Message ingestion task ended with {task.status}: {task.error}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for message ingestion")
            await asyncio.sleep(2)
    print("Getting memory for thread")
    thread_memory = await client.thread.get_context(thread_uuid)
    print(thread_memory)

    print("Getting episodes for user")
    episode_result = await client.graph.episode.list(user_uuid, page_size=3)
    episodes = episode_result.items
    print(f"Episodes for user {user_uuid}:")
    print(episodes)
    episode = await client.graph.episode.get(user_uuid, episodes[0].uuid_)
    print(episode)

    edges = await client.graph.edge.list(user_uuid, page_size=10)
    print(f"Edges for user {user_uuid}:")
    print(edges.items)
    edge = await client.graph.edge.get(user_uuid, edges.items[0].uuid_)
    print(edge)

    nodes = await client.graph.node.list(user_uuid, page_size=10)
    print(f"Nodes for user {user_uuid}:")
    print(nodes.items)
    node = await client.graph.node.get(user_uuid, nodes.items[0].uuid_)
    print(node)

    print("Searching user graph memory...")
    search_results = await client.graph.search_edges(
        user_uuid,
        query="What is the weather in San Francisco?",
        page_size=10,
    )
    print(search_results.items)

    print("Adding a new text episode to the graph...")
    added_episodes = []
    added_episodes.append(await client.graph.episode.add(
        user_uuid,
        type="text",
        data="The user is an avid fan of Eric Clapton",
    ))
    print("Text episode added")
    print("Adding a new JSON episode to the graph...")
    json_data = {
        "name": "Eric Clapton",
        "age": 78,
        "genre": "Rock",
        "favorite_user_uuid": user_uuid,
    }
    json_string = json.dumps(json_data)
    added_episodes.append(await client.graph.episode.add(
        user_uuid,
        type="json",
        data=json_string,
    ))
    print("JSON episode added")
    task_uuid = added_episodes[-1].task.uuid_ if added_episodes[-1].task else None
    if task_uuid:
        deadline = time.monotonic() + 180
        while True:
            task = await client.task.get(task_uuid)
            if task.status == "succeeded":
                break
            if task.status in {"partial", "failed"}:
                raise RuntimeError(f"Episode ingestion task ended with {task.status}: {task.error}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for episode ingestion")
            await asyncio.sleep(2)

    print("Getting nodes from the graph...")
    nodes = await client.graph.node.list(user_uuid, page_size=100)
    nodes = nodes.items
    print(nodes)

    print("Finding Eric Clapton in the graph...")
    clapton_node = [node for node in nodes if node.name == "Eric Clapton"]
    print(clapton_node)

    print("Performing Eric Clapton centered edge search...")
    search_results = await client.graph.search_edges(
        user_uuid,
        query="Eric Clapton",
        center_node_uuid=clapton_node[0].uuid_,
        page_size=10,
    )
    print(search_results.items)

    print("Performing Eric Clapton centered node search...")
    search_results = await client.graph.search_nodes(
        user_uuid,
        query="Eric Clapton",
        center_node_uuid=clapton_node[0].uuid_,
        page_size=10,
    )
    print(search_results.items)


if __name__ == "__main__":
    asyncio.run(main())