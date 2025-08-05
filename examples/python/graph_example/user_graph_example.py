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
    user_id = uuid.uuid4().hex
    thread_id = uuid.uuid4().hex
    await client.user.add(user_id=user_id, first_name="Paul")
    print(f"User {user_id} created")
    await client.thread.create(thread_id=thread_id, user_id=user_id)
    print(f"thread {thread_id} created")
    for message in history[2]:
        await client.thread.add_messages(
            thread_id,
            messages=[
                Message(
                    role=message["role"],
                    name=message["name"],
                    content=message["content"],
                )
            ],
        )

    print("Waiting for the graph to be updated...")
    await asyncio.sleep(30)
    print("Getting memory for thread")
    thread_memory = await client.thread.get_user_context(thread_id)
    print(thread_memory)

    print("Getting episodes for user")
    episode_result = await client.graph.episode.get_by_user_id(user_id, lastn=3)
    episodes = episode_result.episodes
    print(f"Episodes for user {user_id}:")
    print(episodes)
    episode = await client.graph.episode.get(episodes[0].uuid_)
    print(episode)

    edges = await client.graph.edge.get_by_user_id(user_id)
    print(f"Edges for user {user_id}:")
    print(edges)
    edge = await client.graph.edge.get(edges[0].uuid_)
    print(edge)

    nodes = await client.graph.node.get_by_user_id(user_id)
    print(f"Nodes for user {user_id}:")
    print(nodes)
    node = await client.graph.node.get(nodes[0].uuid_)
    print(node)

    print("Searching user graph memory...")
    search_results = await client.graph.search(
        user_id=user_id,
        query="What is the weather in San Francisco?",
    )
    print(search_results.edges)

    print("Adding a new text episode to the graph...")
    await client.graph.add(
        user_id=user_id,
        type="text",
        data="The user is an avid fan of Eric Clapton",
    )
    print("Text episode added")
    print("Adding a new JSON episode to the graph...")
    json_data = {
        "name": "Eric Clapton",
        "age": 78,
        "genre": "Rock",
        "favorite_user_id": user_id,
    }
    json_string = json.dumps(json_data)
    await client.graph.add(
        user_id=user_id,
        type="json",
        data=json_string,
    )
    print("JSON episode added")

    print("Adding a new message episode to the graph...")
    message = "Paul (user): I went to Eric Clapton concert last night"
    await client.graph.add(
        user_id=user_id,
        type="message",
        data=message,
    )
    print("Message episode added")

    print("Waiting for the graph to be updated...")
    # wait for the graph to be updated
    await asyncio.sleep(30)

    print("Getting nodes from the graph...")
    nodes = await client.graph.node.get_by_user_id(user_id)
    print(nodes)

    print("Finding Eric Clapton in the graph...")
    clapton_node = [node for node in nodes if node.name == "Eric Clapton"]
    print(clapton_node)

    print("Performing Eric Clapton centered edge search...")
    search_results = await client.graph.search(
        user_id=user_id,
        query="Eric Clapton",
        center_node_uuid=clapton_node[0].uuid_,
        scope="edges",
    )
    print(search_results.edges)

    print("Performing Eric Clapton centered node search...")
    search_results = await client.graph.search(
        user_id=user_id,
        query="Eric Clapton",
        center_node_uuid=clapton_node[0].uuid_,
        scope="nodes",
    )
    print(search_results.nodes)
    # Note: get_facts() method not available in ZEP v3
    # Facts are now extracted from graph edges as demonstrated above

    # Uncomment to delete the user
    # await client.user.delete(user_id)
    # print(f"User {user_id} deleted")


if __name__ == "__main__":
    asyncio.run(main())