import json
import os

from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import AddMessage

# Load environment variables
load_dotenv()

# Constants. Zep v4 addresses a user by a UUID. Put the UUID of the user that
# the voice agent uses here. The agent prints the UUID when it creates the
# user, and a production application stores it in its own database.
USER_UUID = os.getenv("ZEP_USER_UUID", "")
USER_FULL_NAME = "John Doe"


def load_json_file(filename):
    """Load data from a JSON file."""
    with open(filename) as f:
        return json.load(f)


def populate_user_memory():
    """Create threads and populate them with conversation history."""

    # Initialize Zep client
    zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

    # Load conversation and user data
    user_data = load_json_file("user_data.json")
    conversations = load_json_file("conversations.json")

    # Get the user. The call fails if the UUID is unknown.
    try:
        user = zep_client.user.get(USER_UUID)
    except Exception:
        print(
            f"❌ User with UUID {USER_UUID} does not exist. "
            "Create the user before you populate memory."
        )
        return

    graph_uuid = user.graph_uuid or ""

    # Add user JSON data to the graph of the user, in pieces
    for key, value in user_data.items():
        zep_client.graph.episode.add(graph_uuid, data=json.dumps({key: value}), type="json")

    # Process each conversation thread
    for conversation in conversations:
        messages_data = conversation["messages"]

        try:
            # Create the thread and keep its UUID
            thread = zep_client.thread.create(user_uuid=USER_UUID)
            thread_uuid = thread.uuid_ or ""

            # Convert message data to Zep message objects
            zep_messages = []
            for msg_data in messages_data:
                zep_message = AddMessage(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    name=USER_FULL_NAME if msg_data["role"] == "user" else "Assistant",
                )
                zep_messages.append(zep_message)

            # Add messages to thread
            zep_client.thread.add_messages(thread_uuid, messages=zep_messages)

        except Exception as e:
            print(f"❌ Error processing a thread: {e}")
            continue


if __name__ == "__main__":
    # Validate environment variables
    if not os.getenv("ZEP_API_KEY"):
        print("❌ Missing ZEP_API_KEY environment variable")
        print("Make sure that your .env file contains:")
        print("  ZEP_API_KEY=your_zep_api_key_here")
        exit(1)

    if not USER_UUID:
        print("❌ Missing ZEP_USER_UUID environment variable")
        print("Set it to the UUID of the Zep user that the voice agent uses.")
        exit(1)

    populate_user_memory()
