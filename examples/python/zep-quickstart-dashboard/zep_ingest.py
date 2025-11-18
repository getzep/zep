
import os
from zep_cloud.client import Zep
from zep_cloud.types import Message, EntityEdgeSourceTarget
from dotenv import load_dotenv
import uuid
import json
from ontology import (
    Property, Neighborhood, School, Amenity, FamilyMember, Room, Showing, FinancingDetail,
    InterestedInProperty, ViewedProperty, RejectedProperty, MadeOffer,
    HasRequirement, PrefersNeighborhood, NeedsAmenity, HasBudgetConstraint
)


def create_user(zep_client):
    """Load user config and create user. Raises error if user already exists."""
    # Load user configuration
    with open("data/user.json", "r") as f:
        user_config = json.load(f)
    
    user_id = user_config["user_id_prefix"] + "-" + uuid.uuid4().hex[:6]
    user_email = user_config["email"]
    user_first_name = user_config["first_name"]
    user_last_name = user_config["last_name"]
    
    # Check if user already exists
    try:
        existing_user = zep_client.user.get(user_id=user_id)
        # User exists, raise error
        raise ValueError(f"User {user_id} already exists. Cannot create duplicate user.")
    except ValueError:
        # Re-raise our custom ValueError
        raise
    except Exception:
        # User doesn't exist (API returned an error), which is what we want
        # Continue to create the user
        pass
    
    # Create the user with default ontology disabled
    print(f"\n👤 Creating user {user_id}...")
    user = zep_client.user.add(
        user_id=user_id,
        email=user_email,
        first_name=user_first_name,
        last_name=user_last_name,
        disable_default_ontology=True,
    )
    print(f"✅ User {user_id} created successfully.")

    return user_id, user_first_name, user_last_name


def set_custom_ontology(zep_client, user_id):
    """Set custom real estate ontology for the specific user."""
    print(f"\n🏗️  Setting custom real estate ontology for user {user_id}...")

    try:
        zep_client.graph.set_ontology(
            user_ids=[user_id],
            entities={
                "Property": Property,
                "Neighborhood": Neighborhood,
                "School": School,
                "Amenity": Amenity,
                "FamilyMember": FamilyMember,
                "Room": Room,
                "Showing": Showing,
                "FinancingDetail": FinancingDetail,
            },
            edges={
                "INTERESTED_IN_PROPERTY": (
                    InterestedInProperty,
                    [EntityEdgeSourceTarget(source="User", target="Property")]
                ),
                "VIEWED_PROPERTY": (
                    ViewedProperty,
                    [EntityEdgeSourceTarget(source="User", target="Property")]
                ),
                "REJECTED_PROPERTY": (
                    RejectedProperty,
                    [EntityEdgeSourceTarget(source="User", target="Property")]
                ),
                "MADE_OFFER": (
                    MadeOffer,
                    [EntityEdgeSourceTarget(source="User", target="Property")]
                ),
                "HAS_REQUIREMENT": (
                    HasRequirement,
                    [EntityEdgeSourceTarget(source="User")]
                ),
                "PREFERS_NEIGHBORHOOD": (
                    PrefersNeighborhood,
                    [EntityEdgeSourceTarget(source="User", target="Neighborhood")]
                ),
                "NEEDS_AMENITY": (
                    NeedsAmenity,
                    [EntityEdgeSourceTarget(source="User", target="Amenity")]
                ),
                "HAS_BUDGET_CONSTRAINT": (
                    HasBudgetConstraint,
                    [EntityEdgeSourceTarget(source="User")]
                ),
            }
        )
        print(f"✅ Custom ontology set successfully for user {user_id}")
    except Exception as e:
        print(f"❌ Error setting custom ontology: {e}")
        raise


def ingest_user_data(zep_client, user_id, user_first_name, user_last_name):
    """Ingest user data into graph, adding user_id and user_full_name to each piece."""
    user_full_name = f"{user_first_name} {user_last_name}"
    
    # Load user data
    with open("data/user_data.json", "r") as f:
        user_data = json.load(f)
    
    print("\n📊 Adding user data to graph...")
    for item in user_data:
        # Add user_id and user_full_name to each piece of JSON
        # Each item is a dict with one key (e.g., "house_search") containing the data
        for key, data_dict in item.items():
            data_dict["user_id"] = user_id
            data_dict["user_full_name"] = user_full_name
        
        try:
            zep_client.graph.add(
                user_id=user_id,
                data=json.dumps(item),
                type="json"
            )
            # Get the key name for logging
            key_name = list(item.keys())[0]
            print(f"✅ Successfully added {key_name} data to graph")
        except Exception as e:
            key_name = list(item.keys())[0] if item else "unknown"
            print(f"❌ Error adding {key_name} data to graph: {e}")


def ingest_conversations(zep_client, user_id, user_first_name, user_last_name):
    """Ingest conversations into threads."""
    # Load conversations
    with open("data/conversations.json", "r") as f:
        conversations = json.load(f)
    
    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    for conversation in conversations:
        thread_id = f"conversation-{uuid.uuid4().hex[:8]}"
        messages_data = conversation["messages"]
        
        try:
            # Create thread
            zep_client.thread.create(
                thread_id=thread_id,
                user_id=user_id
            )
            
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
            zep_client.thread.add_messages(
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

    # Create user
    user_id, user_first_name, user_last_name = create_user(zep_client)

    # Set custom ontology for this specific user
    set_custom_ontology(zep_client, user_id)

    # Ingest user data
    ingest_user_data(zep_client, user_id, user_first_name, user_last_name)

    # Ingest conversations
    ingest_conversations(zep_client, user_id, user_first_name, user_last_name)
