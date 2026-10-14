import json
import os
from pathlib import Path

from dotenv import load_dotenv
from zep_cloud import Zep
from zep_cloud.types import Message

load_dotenv()

EXAMPLE_DIR = Path(__file__).parent


def load_json(filename: str) -> object:
    with (EXAMPLE_DIR / filename).open() as file:
        return json.load(file)


def populate_user_memory() -> None:
    zep = Zep(api_key=os.environ["ZEP_API_KEY"])
    graph_uuid = os.environ["ZEP_USER_UUID"]
    user_data = load_json("user_data.json")
    conversations = load_json("conversations.json")

    if not isinstance(user_data, dict) or not isinstance(conversations, list):
        raise ValueError("The example JSON files have an invalid format.")

    for key, value in user_data.items():
        zep.graph.episode.add(
            graph_uuid,
            data=json.dumps({key: value}),
            type="json",
        )

    for conversation in conversations:
        messages = [
            Message(
                role=message["role"],
                content=message["content"],
                name="User" if message["role"] == "user" else "Assistant",
            )
            for message in conversation["messages"]
        ]
        thread = zep.thread.create(user_uuid=graph_uuid)
        zep.thread.add_messages(thread.uuid_, messages=messages)
        print(f"Created thread {thread.uuid_}; save this UUID in your application.")


if __name__ == "__main__":
    if not os.getenv("ZEP_API_KEY") or not os.getenv("ZEP_USER_UUID"):
        raise SystemExit("Set ZEP_API_KEY and ZEP_USER_UUID.")
    populate_user_memory()
