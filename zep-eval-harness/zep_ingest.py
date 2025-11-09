import os
import sys
import json
import glob
import uuid
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message
from zep_cloud import EpisodeData

# Import ontology module
try:
    from ontology import set_custom_ontology, ENTITY_TYPES, EDGE_TYPES

    CUSTOM_ONTOLOGY_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_ONTOLOGY_AVAILABLE = False
    ENTITY_TYPES = []
    EDGE_TYPES = []


def load_user_definitions():
    """
    Load user definitions from data/users.json.
    Returns list of user definition dictionaries.
    """
    users_file = "data/users.json"

    if not os.path.exists(users_file):
        print(f"Error: {users_file} not found")
        exit(1)

    with open(users_file, "r") as f:
        users = json.load(f)

    return users


async def create_user(zep_client, user_definition, disable_default_ontology=False):
    """
    Create a new user with a randomized ID suffix to make ingestion idempotent.
    If custom ontology is enabled, applies it to the user before returning.

    Args:
        zep_client: AsyncZep client instance
        user_definition: User definition dict
        disable_default_ontology: If True, suppress default Zep ontology for this user

    Returns tuple of (actual_user_id, base_user_id, random_suffix).
    """
    base_user_id = user_definition["user_id"]

    # Add random suffix to make each ingestion run unique
    random_suffix = uuid.uuid4().hex[:8]
    user_id = f"{base_user_id}_{random_suffix}"

    user = await zep_client.user.add(
        user_id=user_id,
        first_name=user_definition["first_name"],
        last_name=user_definition.get("last_name"),
        email=user_definition.get("email"),
        disable_default_ontology=disable_default_ontology,
    )

    print(f"✓ User {user_id} created successfully")
    print(
        f"   Name: {user_definition['first_name']} {user_definition.get('last_name', '')}"
    )
    print(f"   Base ID: {base_user_id}")
    print(f"   Suffix: {random_suffix}")
    if disable_default_ontology:
        print(f"   Default ontology: DISABLED")

    # Apply custom ontology to this user BEFORE ingesting data
    if disable_default_ontology:
        print("\nSetting up custom ontology...")
        print(f"Applying to user: {user_id}")
        try:
            await set_custom_ontology(zep_client, user_ids=[user_id])
            print("✓ Custom ontology applied successfully\n")
        except Exception as e:
            print(f"Error setting ontology: {e}")
            raise

    return user_id, base_user_id, random_suffix


def load_conversations_for_user(user_id):
    """
    Load all conversation files for a specific user.
    Returns list of conversation data dictionaries.
    """
    pattern = f"data/conversations/{user_id}_*.json"
    conversation_files = glob.glob(pattern)

    if not conversation_files:
        print(f"Warning: No conversation files found for user {user_id}")
        return []

    conversations = []
    for file_path in conversation_files:
        with open(file_path, "r") as f:
            conversation = json.load(f)
            conversations.append(conversation)

    print(f"✓ Loaded {len(conversations)} conversation(s) for user {user_id}")
    return conversations


def load_telemetry_for_user(user_id):
    """
    Load all telemetry files for a specific user.
    Returns list of telemetry data dictionaries.
    """
    pattern = f"data/telemetry/{user_id}_*.json"
    telemetry_files = glob.glob(pattern)

    if not telemetry_files:
        return []

    telemetry_data = []
    for file_path in telemetry_files:
        with open(file_path, "r") as f:
            data = json.load(f)
            telemetry_data.append(data)

    print(f"✓ Loaded {len(telemetry_data)} telemetry file(s) for user {user_id}")
    return telemetry_data


async def add_conversations_to_zep(
    zep_client: AsyncZep, user_id: str, conversations: list[dict], suffix: str
) -> list[str]:
    """
    Add conversations to Zep as separate threads using batch API.
    Uses thread.add_messages_batch for concurrent processing (useful for data migrations).
    Does not wait for processing to complete.
    Returns list of created thread IDs.
    """
    total_messages = 0
    thread_ids = []

    # Count total messages
    for conversation in conversations:
        total_messages += len(conversation.get("messages", []))

    print(f"\nTotal messages to add for {user_id}: {total_messages}")

    # Process each conversation as a separate thread
    for idx, conversation in enumerate(conversations, 1):
        conversation_id = conversation.get("conversation_id", f"conv_{idx}")
        messages_data = conversation.get("messages", [])

        if not messages_data:
            print(
                f"Warning: Skipping conversation {conversation_id} - no messages found"
            )
            continue

        try:
            # Create thread for this conversation with unique suffix
            thread_id = f"{conversation_id}_{suffix}"
            await zep_client.thread.create(thread_id=thread_id, user_id=user_id)
            print(f"Created thread: {thread_id}")
            thread_ids.append(thread_id)

            # Convert messages to Zep Message objects
            zep_messages = []
            for msg in messages_data:
                zep_message = Message(
                    role=msg["role"],
                    content=msg["content"],
                    created_at=msg.get("timestamp"),  # Optional timestamp
                )
                zep_messages.append(zep_message)

            # Use batch API for concurrent processing - designed for data migrations
            # Batch in groups of 30 messages to be safe
            batch_size = 30
            total_added = 0
            for i in range(0, len(zep_messages), batch_size):
                batch = zep_messages[i : i + batch_size]
                await zep_client.thread.add_messages(
                    thread_id=thread_id, messages=batch
                )
                total_added += len(batch)

            print(f"✓ Added {total_added} messages to thread {thread_id} (batch mode)")

        except Exception as e:
            print(f"Error processing conversation {conversation_id}: {e}")
            continue

    return thread_ids


async def add_telemetry_to_zep(
    zep_client: AsyncZep, user_id: str, telemetry_data: list[dict]
) -> None:
    """
    Add telemetry data to Zep using graph.add_batch for concurrent processing.
    Batch API is suitable for static data without temporal dependencies.
    Does not wait for processing to complete.
    """
    if not telemetry_data:
        return

    print(f"\nAdding {len(telemetry_data)} telemetry file(s) for {user_id}")

    # Convert telemetry data to EpisodeData objects
    episodes = []
    for telemetry in telemetry_data:
        data_type = telemetry.get("data_type", "unknown")
        try:
            episode = EpisodeData(data=json.dumps(telemetry), type="json")
            episodes.append(episode)
        except Exception as e:
            print(f"Error preparing telemetry episode {data_type}: {e}")
            continue

    if not episodes:
        print("No valid telemetry episodes to add")
        return

    # Add all episodes in batch (max 20 episodes at a time per Zep API limit)
    batch_size = 20
    total_added = 0

    for i in range(0, len(episodes), batch_size):
        batch = episodes[i : i + batch_size]
        try:
            await zep_client.graph.add_batch(episodes=batch, user_id=user_id)
            total_added += len(batch)
            print(
                f"✓ Added batch of {len(batch)} telemetry episodes (total: {total_added}/{len(episodes)})"
            )
        except Exception as e:
            print(f"Error adding telemetry batch: {e}")
            continue

    print(f"✓ Completed adding {total_added} telemetry episodes in batch mode")


def get_next_run_number():
    """
    Get the next run number by checking existing run directories.
    """
    os.makedirs("runs", exist_ok=True)
    existing_runs = glob.glob("runs/*")

    if not existing_runs:
        return 1

    # Extract run numbers from directory names (format: runs/1_timestamp)
    run_numbers = []
    for run_dir in existing_runs:
        try:
            # Get just the directory name without path
            dir_name = os.path.basename(run_dir)
            # Skip .gitkeep and other non-directory files
            if not os.path.isdir(run_dir):
                continue
            # Extract number before first underscore
            run_num = int(dir_name.split("_")[0])
            run_numbers.append(run_num)
        except (IndexError, ValueError):
            continue

    return max(run_numbers) + 1 if run_numbers else 1


def write_run_manifest(run_number, run_data, use_custom_ontology=False):
    """
    Write a manifest file for the current run.
    Format: runs/{number}_{ISO8601_timestamp}/

    Args:
        run_number: Run number
        run_data: List of user data dicts
        use_custom_ontology: Whether custom ontology was used
    """
    timestamp = datetime.now().isoformat()
    # Use ISO 8601 format with basic format (no colons for filesystem compatibility)
    timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = f"runs/{run_number}_{timestamp_str}"
    os.makedirs(run_dir, exist_ok=True)

    manifest = {
        "run_number": run_number,
        "timestamp": timestamp,
        "ontology": {
            "type": (
                "custom" if use_custom_ontology else "default_zep"
            ),
            "default_ontology_disabled": use_custom_ontology,
            "custom_entity_types": ENTITY_TYPES if use_custom_ontology else [],
            "custom_edge_types": EDGE_TYPES if use_custom_ontology else [],
        },
        "users": run_data,
    }

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, indent=2, fp=f)

    print(f"\nRun manifest written to: {manifest_path}")
    return run_dir


async def main():
    # Load environment variables
    load_dotenv()

    # Parse command-line arguments
    use_custom_ontology = False
    if len(sys.argv) > 1 and sys.argv[1] == "--custom-ontology":
        if not CUSTOM_ONTOLOGY_AVAILABLE:
            print("Error: Custom ontology module could not be loaded")
            print("   Check that ontology.py exists and is valid")
            exit(1)
        use_custom_ontology = True

    # Validate environment variable
    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        print("   Please create a .env file with your ZEP_API_KEY")
        exit(1)

    # Initialize AsyncZep client (singleton instance)
    zep_client = AsyncZep(api_key=api_key)

    print("=" * 80)
    print("ZEP INGESTION SCRIPT")
    print("=" * 80)
    if use_custom_ontology:
        print("Mode: Custom ontology (default ontology suppressed)")
        print(
            "Features: Person, Location, Organization, Event, and Item entities with relationship edges"
        )
    else:
        print("Mode: Default Zep ontology")
    print("=" * 80)

    try:
        # Get next run number
        run_number = get_next_run_number()
        print(f"\nStarting run #{run_number}\n")

        # Load user definitions from users.json
        user_definitions = load_user_definitions()

        if not user_definitions:
            print("Error: No users found in data/users.json")
            exit(1)

        print(f"\nFound {len(user_definitions)} user(s) in data/users.json\n")

        # Process all users - create users and add data without waiting
        print("Creating users and adding data (no polling for completion)...\n")

        run_data = []

        for user_def in user_definitions:
            base_user_id = user_def["user_id"]
            print("=" * 80)
            print(
                f"Processing user: {user_def['first_name']} {user_def.get('last_name', '')}"
            )
            print("=" * 80)

            # Step 1: Create user with randomized ID (and apply custom ontology if enabled)
            actual_user_id, base_user_id, suffix = await create_user(
                zep_client, user_def, disable_default_ontology=use_custom_ontology
            )

            # Step 2: Load conversations
            conversations = load_conversations_for_user(base_user_id)

            # Step 3: Load telemetry
            telemetry_data = load_telemetry_for_user(base_user_id)

            # Step 4: Add conversations to Zep (no waiting)
            thread_ids = []
            if conversations:
                thread_ids = await add_conversations_to_zep(
                    zep_client, actual_user_id, conversations, suffix
                )

            # Step 5: Add telemetry to Zep (no waiting)
            if telemetry_data:
                await add_telemetry_to_zep(zep_client, actual_user_id, telemetry_data)

            # Collect data for manifest
            run_data.append(
                {
                    "base_user_id": base_user_id,
                    "zep_user_id": actual_user_id,
                    "first_name": user_def["first_name"],
                    "last_name": user_def.get("last_name"),
                    "thread_ids": thread_ids,
                    "num_conversations": len(conversations),
                    "num_telemetry_files": len(telemetry_data) if telemetry_data else 0,
                }
            )

            print(f"✓ Data submitted for user {actual_user_id}\n")

        # Write run manifest
        run_dir = write_run_manifest(run_number, run_data, use_custom_ontology)

        print("=" * 80)
        print("INGESTION COMPLETE")
        print("=" * 80)
        print(f"\nRun #{run_number}")
        print(f"Manifest: {run_dir}/manifest.json")
        print(f"Users: {len(user_definitions)}")
        print("\nAll user(s) and their data have been submitted to Zep.")
        print(
            "Note: Graph processing happens asynchronously and may take several minutes."
        )
        print(
            f"You can run zep_evaluate.py with run #{run_number} once processing is complete."
        )

    except Exception as e:
        print(f"\nScript failed: {e}")
        exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["-h", "--help"]:
        print("Usage: python zep_ingest.py [--custom-ontology]")
        print()
        print("Options:")
        print("  --custom-ontology    Use custom ontology instead of Zep defaults")
        print(
            "                       Applies custom entity/edge types to ingested users"
        )
        print()
        print("Examples:")
        print("  python zep_ingest.py                    # Use default Zep ontology")
        print("  python zep_ingest.py --custom-ontology  # Use custom ontology")
        exit(0)

    asyncio.run(main())
