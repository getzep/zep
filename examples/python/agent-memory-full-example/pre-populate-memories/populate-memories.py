
import os
import time
from zep_cloud.client import Zep
from zep_cloud.types import Message
from dotenv import load_dotenv
import uuid
import json

USER_ID = "John-12345"
USER_EMAIL = "john@example.com"
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"

def create_user(zep_client):
    """Create a user for this example run."""
    user = zep_client.user.create(
        user_id=f"{USER_ID}-{uuid.uuid4().hex[:8]}",
        email=USER_EMAIL,
        first_name=USER_FIRST_NAME,
        last_name=USER_LAST_NAME,
    )
    print(f"User created. Set ZEP_USER_UUID={user.uuid_} for the companion app.")
    return user



def load_conversations():
    """Load conversations from JSON file."""
    with open("conversations.json", "r") as f:
        return json.load(f)

def load_user_data():
    """Load user data from JSON file."""
    with open("user_data.json", "r") as f:
        return json.load(f)

def populate_user_memory(zep_client):
    """Create threads and populate them with conversation history and user data."""

    # Load conversation data and user data
    conversations = load_conversations()
    user_data = load_user_data()

    # Ensure user exists
    user = create_user(zep_client)
    last_task_uuid = None

    # Add user data to graph in pieces
    print("\n📊 Adding user data to graph...")
    for item in user_data:
        try:
            result = zep_client.graph.episode.add(
                user.uuid_,
                data=json.dumps(item),
                type="json"
            )
            # Get the key name for logging
            key_name = list(item.keys())[0]
            print(f"✅ Added {key_name} as episode {result.episode.uuid_}")
            last_task_uuid = result.task.uuid_ if result.task else last_task_uuid
        except Exception as e:
            key_name = list(item.keys())[0] if item else "unknown"
            print(f"❌ Error adding {key_name} data to graph: {e}")

    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    for conversation in conversations:
        messages_data = conversation["messages"]
        
        try:
            # Create thread
            thread = zep_client.thread.create(user_uuid=user.uuid_)
            
            # Convert message data to Zep Message objects
            zep_messages = []
            for msg_data in messages_data:
                zep_message = Message(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    name=f"{USER_FIRST_NAME} {USER_LAST_NAME}" if msg_data["role"] == "user" else "Assistant"
                )
                zep_messages.append(zep_message)
            
            # Add messages to thread
            result = zep_client.thread.add_messages(
                thread_uuid=thread.uuid_,
                messages=zep_messages
            )
            last_task_uuid = result.task.uuid_ if result.task else last_task_uuid
            print(f"✅ Successfully added messages to thread {thread.uuid_}")
            
        except Exception as e:
            print(f"❌ Error processing thread: {e}")
            continue
    if last_task_uuid:
        deadline = time.monotonic() + 1200
        next_status_report = time.monotonic() + 30
        while True:
            task = zep_client.task.get(last_task_uuid)
            if task.status == "succeeded":
                break
            if task.status == "partial":
                print(f"Warning: task {last_task_uuid} ended with partial status: {task.error}")
                break
            if task.status == "failed":
                raise RuntimeError(f"Ingestion task ended with failed status: {task.error}")
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Timed out waiting for task {last_task_uuid}")
            if now >= next_status_report:
                print(f"Task {last_task_uuid} status={task.status}; still waiting.")
                next_status_report = now + 30
            time.sleep(2)

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Validate environment variables
    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("❌ Missing ZEP_API_KEY environment variable")
        exit(1)
    
    # Initialize Zep client
    zep_client = Zep(api_key=api_key)
    
    # Populate user memory
    populate_user_memory(zep_client)