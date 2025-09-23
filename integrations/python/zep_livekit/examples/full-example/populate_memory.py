import json
import os
import uuid
from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import Message

# Load environment variables
load_dotenv()

# Constants - assume this user already exists in Zep
USER_ID = "John-1234"
USER_FULL_NAME = "John Doe"

def load_conversations():
    """Load conversations from JSON file."""
    with open("conversations.json", "r") as f:
        return json.load(f)

def populate_user_memory():
    """Create threads and populate them with conversation history."""
    
    # Initialize Zep client
    zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))
    
    # Load conversation data
    conversations = load_conversations()
    
    # Get user, if it doesn't exist, throw error
    try:
        zep_client.user.get(user_id=USER_ID)
    except Exception:
        # Throw error
        print(f"❌ User with ID {USER_ID} does not exist. Please create the user before populating memory.")
        return
    
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
                    name=USER_FULL_NAME if msg_data["role"] == "user" else "Assistant"
                )
                zep_messages.append(zep_message)
            
            # Add messages to thread
            zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=zep_messages
            )
            
        except Exception as e:
            print(f"❌ Error processing thread {thread_id}: {e}")
            continue

if __name__ == "__main__":
    # Validate environment variables
    if not os.getenv("ZEP_API_KEY"):
        print("❌ Missing ZEP_API_KEY environment variable")
        print("Please ensure your .env file contains:")
        print("  ZEP_API_KEY=your_zep_api_key_here")
        exit(1)
    
    populate_user_memory()