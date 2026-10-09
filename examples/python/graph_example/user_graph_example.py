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
import json
from dotenv import find_dotenv, load_dotenv
from conversations import history

from zep_cloud import AddMessage
from zep_cloud.client import AsyncZep

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )
    # v4 returns the UUID of the user and the UUID of the graph of the user.
    # Every later call uses those UUIDs.
    user = await client.user.create(first_name="Paul")
    user_uuid = user.uuid_
    graph_uuid = user.graph_uuid
    print(f"User {user_uuid} created")
    thread = await client.thread.create(user_uuid=user_uuid)
    thread_uuid = thread.uuid_
    print(f"thread {thread_uuid} created")
    for message in history[2]:
        await client.thread.add_messages(
            thread_uuid,
            messages=[
                AddMessage(
                    role=message["role"],
                    name=message["name"],
                    content=message["content"],
                )
            ],
        )

    print("Waiting for the graph to be updated...")
    await asyncio.sleep(30)
    print("Getting memory for thread")
    thread_memory = await client.thread.get_context(thread_uuid)
    print(thread_memory)

    print("Getting episodes for user")
    episodes = [
        episode async for episode in await client.graph.episode.list(graph_uuid, limit=3)
    ]
    print(f"Episodes for user {user_uuid}:")
    print(episodes)
    episode = await client.graph.episode.get(graph_uuid, episodes[0].uuid_)
    print(episode)

    edges = [edge async for edge in await client.graph.edge.list(graph_uuid)]
    print(f"Edges for user {user_uuid}:")
    print(edges)
    edge = await client.graph.edge.get(graph_uuid, edges[0].uuid_)
    print(edge)

    nodes = [node async for node in await client.graph.node.list(graph_uuid)]
    print(f"Nodes for user {user_uuid}:")
    print(nodes)
    node = await client.graph.node.get(graph_uuid, nodes[0].uuid_)
    print(node)

    print("Searching user graph memory...")
    search_results = [
        edge
        async for edge in await client.graph.search_edges(
            graph_uuid,
            query="What is the weather in San Francisco?",
        )
    ]
    print(search_results)

    print("Adding a new text episode to the graph...")
    await client.graph.episode.add(
        graph_uuid,
        type="text",
        data="The user is an avid fan of Eric Clapton",
    )
    print("Text episode added")
    print("Adding a new JSON episode to the graph...")
    json_data = {
        "name": "Eric Clapton",
        "age": 78,
        "genre": "Rock",
        "favorite_user_uuid": user_uuid,
    }
    json_string = json.dumps(json_data)
    await client.graph.episode.add(
        graph_uuid,
        type="json",
        data=json_string,
    )
    print("JSON episode added")

    print("Adding a new message episode to the graph...")
    message = "Paul (user): I went to Eric Clapton concert last night"
    await client.graph.episode.add(
        graph_uuid,
        type="message",
        data=message,
    )
    print("Message episode added")

    print("Waiting for the graph to be updated...")
    # wait for the graph to be updated
    await asyncio.sleep(30)

    print("Getting nodes from the graph...")
    nodes = [node async for node in await client.graph.node.list(graph_uuid)]
    print(nodes)

    print("Finding Eric Clapton in the graph...")
    clapton_node = [node for node in nodes if node.name == "Eric Clapton"]
    print(clapton_node)

    print("Performing Eric Clapton centered edge search...")
    edge_results = [
        edge
        async for edge in await client.graph.search_edges(
            graph_uuid,
            query="Eric Clapton",
            center_node_uuid=clapton_node[0].uuid_,
        )
    ]
    print(edge_results)

    print("Performing Eric Clapton centered node search...")
    node_results = [
        node
        async for node in await client.graph.search_nodes(
            graph_uuid,
            query="Eric Clapton",
            center_node_uuid=clapton_node[0].uuid_,
        )
    ]
    print(node_results)

    # Uncomment to delete the user
    # await client.user.delete(user_uuid)
    # print(f"User {user_uuid} deleted")


if __name__ == "__main__":
    asyncio.run(main())