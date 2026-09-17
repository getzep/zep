
import os
from zep_cloud import AddMessage
from zep_cloud.client import Zep
from zep_cloud.types import UserInstruction
from dotenv import load_dotenv
import json


def create_user(zep_client):
    """Load the user config and create the user.

    v4 gives every user a server-generated UUID. The example does not send a
    user_id.
    """
    # Load user configuration
    with open("data/user.json", "r") as f:
        user_config = json.load(f)

    user_email = user_config["email"]
    user_first_name = user_config["first_name"]
    user_last_name = user_config["last_name"]

    # Create the user
    print("\n👤 Creating user...")
    user = zep_client.user.create(
        email=user_email,
        first_name=user_first_name,
        last_name=user_last_name,
    )
    print(f"✅ User {user.uuid_} created successfully.")

    return user, user_first_name, user_last_name


def set_user_summary_instructions(zep_client, user_uuid):
    """Set user summary instructions."""
    print("\n🎯 Setting user-specific custom user summary instructions...")
    try:
        # v4 sets the summary instructions of one user, which the user UUID
        # selects.
        zep_client.user.set_summary_instructions(
            user_uuid,
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


def ingest_user_data(zep_client, user_uuid, graph_uuid, user_first_name, user_last_name):
    """Ingest user data into graph, adding the user UUID and the full name to each piece."""
    user_full_name = f"{user_first_name} {user_last_name}"
    
    # Load user data
    with open("data/user_data.json", "r") as f:
        user_data = json.load(f)
    
    print("\n📊 Adding user data to graph...")
    for item in user_data:
        # Add the user UUID and the full name to each piece of JSON
        # Each item is a dict with one key (e.g., "house_search") containing the data
        for key, data_dict in item.items():
            data_dict["user_uuid"] = user_uuid
            data_dict["user_full_name"] = user_full_name
        
        try:
            zep_client.graph.episode.add(
                graph_uuid,
                data=json.dumps(item),
                type="json"
            )
            # Get the key name for logging
            key_name = list(item.keys())[0]
            print(f"✅ Successfully added {key_name} data to graph")
        except Exception as e:
            key_name = list(item.keys())[0] if item else "unknown"
            print(f"❌ Error adding {key_name} data to graph: {e}")


def ingest_conversations(zep_client, user_uuid, user_first_name, user_last_name):
    """Ingest conversations into threads."""
    # Load conversations
    with open("data/conversations.json", "r") as f:
        conversations = json.load(f)
    
    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    for conversation in conversations:
        thread_uuid = None
        messages_data = conversation["messages"]
        
        try:
            # Create thread
            thread = zep_client.thread.create(
                user_uuid=user_uuid
            )
            thread_uuid = thread.uuid_
            
            # Convert message data to Zep Message objects
            zep_messages = []
            for msg_data in messages_data:
                zep_message = AddMessage(
                    role=msg_data["role"],
                    content=msg_data["content"],
                    name=f"{user_first_name} {user_last_name}" if msg_data["role"] == "user" else "Assistant"
                )
                zep_messages.append(zep_message)
            
            # Add messages to thread
            zep_client.thread.add_messages(
                thread_uuid,
                messages=zep_messages
            )
            print(f"✅ Successfully added messages to thread {thread_uuid}")
            
        except Exception as e:
            print(f"❌ Error processing thread {thread_uuid}: {e}")
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
    
    # Create user
    user, user_first_name, user_last_name = create_user(zep_client)
    
    # Set user summary instructions
    set_user_summary_instructions(zep_client, user.uuid_)
    
    # Ingest user data
    ingest_user_data(zep_client, user.uuid_, user.graph_uuid, user_first_name, user_last_name)
    
    # Ingest conversations
    ingest_conversations(zep_client, user.uuid_, user_first_name, user_last_name)
