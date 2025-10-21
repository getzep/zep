
import os
from zep_cloud.client import Zep
from zep_cloud.types import Message
from dotenv import load_dotenv
import uuid
import json

USER_ID = "John-12345"
USER_EMAIL = "john@example.com"
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"

def get_or_create_user(zep_client):
    """Get an existing user or create a new one if they don't exist."""
    try:
        user = zep_client.user.get(user_id=USER_ID)
        print(f"User {USER_ID} already exists. Continuing with existing user.")
    except Exception:
        user = zep_client.user.add(
            user_id=USER_ID,
            email=USER_EMAIL,
            first_name=USER_FIRST_NAME,
            last_name=USER_LAST_NAME,
        )
        print(f"User {USER_ID} created.")
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
    user = get_or_create_user(zep_client)

    # Add user data to graph in pieces
    print("\n📊 Adding user data to graph...")
    for item in user_data:
        try:
            zep_client.graph.add(
                user_id=USER_ID,
                data=json.dumps(item),
                type="json"
            )
            # Get the key name for logging
            key_name = list(item.keys())[0]
            print(f"✅ Successfully added {key_name} data to graph")
        except Exception as e:
            key_name = list(item.keys())[0] if item else "unknown"
            print(f"❌ Error adding {key_name} data to graph: {e}")

    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    for conversation in conversations:
        thread_id = f"conversation-{uuid.uuid4().hex[:8]}"
        messages_data = conversation["messages"]
        
        try:
            # Create thread
            zep_client.thread.create(
                thread_id=thread_id,
                user_id=USER_ID
            )
            
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
            zep_client.thread.add_messages_batch(
                thread_id=thread_id,
                messages=zep_messages
            )
            print(f"✅ Successfully added messages to thread {thread_id}")
            
        except Exception as e:
            print(f"❌ Error processing thread {thread_id}: {e}")
            continue

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