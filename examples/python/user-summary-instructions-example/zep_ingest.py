
import os
import time
from zep_cloud.client import Zep
from zep_cloud.types import Message
from zep_cloud.types import UserInstruction
from dotenv import load_dotenv
import json


def create_user(zep_client):
    """Create a user from the local demo configuration."""
    # Load user configuration
    with open("data/user.json", "r") as f:
        user_config = json.load(f)
    
    user_email = user_config["email"]
    user_first_name = user_config["first_name"]
    user_last_name = user_config["last_name"]

    user_uuid = os.getenv("ZEP_USER_UUID")
    if user_uuid:
        print(f"\nUsing user UUID from ZEP_USER_UUID: {user_uuid}")
        return user_uuid, user_first_name, user_last_name

    print("\n👤 Creating user...")
    user = zep_client.user.create(
        email=user_email,
        first_name=user_first_name,
        last_name=user_last_name,
    )
    print(f"✅ User created. Set ZEP_USER_UUID={user.uuid_} for the companion app.")
    
    return user.uuid_, user_first_name, user_last_name


def set_user_summary_instructions(zep_client, user_id):
    """Set user summary instructions."""
    print("\n🎯 Setting user-specific custom user summary instructions...")
    try:
        zep_client.user.set_summary_instructions(
            user_id,
            instructions=[
                UserInstruction(
                    name="price_range",
                    text="What is the user's budget or price range for purchasing a home?",
                ),
                UserInstruction(
                    name="bedroom_requirements",
                    text="How many bedrooms does the user need and why?",
                ),
                UserInstruction(
                    name="must_have_features",
                    text="What are the user's must-have features in a home?",
                ),
                UserInstruction(
                    name="preferred_features",
                    text="What features does the user prefer but are not deal-breakers?",
                ),
                UserInstruction(
                    name="family_context",
                    text="What are the user's key family details that impact their housing needs?",
                ),
            ],
        )
        print("✅ Successfully set custom user summary instructions")
    except Exception as e:
        print(f"❌ Error setting user summary instructions: {e}")


def ingest_user_data(zep_client, user_id, user_first_name, user_last_name):
    """Ingest user data into graph, adding user_id and user_full_name to each piece."""
    user_full_name = f"{user_first_name} {user_last_name}"
    
    # Load user data
    with open("data/user_data.json", "r") as f:
        user_data = json.load(f)
    
    print("\n📊 Adding user data to graph...")
    last_task_uuid = None
    for item in user_data:
        # Add user_id and user_full_name to each piece of JSON
        # Each item is a dict with one key (e.g., "house_search") containing the data
        for key, data_dict in item.items():
            data_dict["user_id"] = user_id
            data_dict["user_full_name"] = user_full_name
        
        try:
            result = zep_client.graph.episode.add(
                user_id,
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
    return last_task_uuid


def ingest_conversations(
    zep_client, user_id, user_first_name, user_last_name, last_task_uuid=None
):
    """Ingest conversations into threads."""
    # Load conversations
    with open("data/conversations.json", "r") as f:
        conversations = json.load(f)
    
    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    for conversation in conversations:
        messages_data = conversation["messages"]
        
        try:
            # Create thread
            thread = zep_client.thread.create(user_uuid=user_id)
            
            # Convert message data to Zep Message objects
            zep_messages = []
            for msg_data in messages_data:
                zep_message = Message(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    name=f"{user_first_name} {user_last_name}" if msg_data["role"] == "user" else "Assistant"
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
    
    # Create user
    user_id, user_first_name, user_last_name = create_user(zep_client)
    
    # Set user summary instructions
    set_user_summary_instructions(zep_client, user_id)
    
    # Ingest user data
    last_task_uuid = ingest_user_data(
        zep_client, user_id, user_first_name, user_last_name
    )
    
    # Ingest conversations
    ingest_conversations(
        zep_client, user_id, user_first_name, user_last_name, last_task_uuid
    )
