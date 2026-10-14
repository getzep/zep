
import os
import time
import json
from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.ontology import build_ontology
from zep_cloud.types import EdgeSourceTarget, Message
from ontology import *


def create_user(zep_client):
    """Create a user or use the UUID configured for the companion app."""
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

    # Create the user with default ontology disabled
    print("\n👤 Creating user...")
    user = zep_client.user.create(
        email=user_email,
        first_name=user_first_name,
        last_name=user_last_name,
        disable_default_ontology=True,
    )
    print(f"✅ User created. Set ZEP_USER_UUID={user.uuid_} for the companion app.")

    return user.uuid_, user_first_name, user_last_name


def set_custom_ontology(zep_client, user_id):
    """Set custom real estate ontology for the specific user."""
    print(f"\n🏗️  Setting custom real estate ontology for user {user_id}...")

    try:
        entity_types, edge_types = build_ontology(
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
                    [EdgeSourceTarget(source="User", target="Property")]
                ),
                "VIEWED_PROPERTY": (
                    ViewedProperty,
                    [EdgeSourceTarget(source="User", target="Property")]
                ),
                "REJECTED_PROPERTY": (
                    RejectedProperty,
                    [EdgeSourceTarget(source="User", target="Property")]
                ),
                "MADE_OFFER": (
                    MadeOffer,
                    [EdgeSourceTarget(source="User", target="Property")]
                ),
                "HAS_REQUIREMENT": (
                    HasRequirement,
                    [EdgeSourceTarget(source="User")]
                ),
                "PREFERS_NEIGHBORHOOD": (
                    PrefersNeighborhood,
                    [EdgeSourceTarget(source="User", target="Neighborhood")]
                ),
                "NEEDS_AMENITY": (
                    NeedsAmenity,
                    [EdgeSourceTarget(source="User", target="Amenity")]
                ),
                "HAS_BUDGET_CONSTRAINT": (
                    HasBudgetConstraint,
                    [EdgeSourceTarget(source="User")]
                ),
            }
        )
        zep_client.graph.set_ontology(user_id, entity_types=entity_types, edge_types=edge_types)
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



def ingest_conversations(zep_client, user_id, user_first_name, user_last_name):
    """Ingest conversations into threads."""
    # Load conversations
    with open("data/conversations.json", "r") as f:
        conversations = json.load(f)

    print("\n💬 Adding conversations to threads...")
    # Process each conversation thread
    last_task_uuid = None
    for conversation in conversations:
        messages_data = conversation["messages"]

        try:
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
                thread_uuid=thread.uuid_, messages=zep_messages
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

    # Set custom ontology for this specific user
    set_custom_ontology(zep_client, user_id)

    # Ingest user data
    ingest_user_data(zep_client, user_id, user_first_name, user_last_name)

    # Ingest conversations
    ingest_conversations(zep_client, user_id, user_first_name, user_last_name)
