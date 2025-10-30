import os
import json
import time
from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import Message

# Hardcoded user ID for evaluation
USER_ID = "zep_eval_test_user_001"
USER_FIRST_NAME = "John"
USER_LAST_NAME = "Doe"
USER_EMAIL = "john.doe@example.com"


def create_user(zep_client):
    """Create a new user. Raises error if user already exists."""
    try:
        existing_user = zep_client.user.get(user_id=USER_ID)
        raise Exception(
            f"Error: User {USER_ID} already exists. "
            "Please delete the existing user or use a different user ID. "
            "We do not continue with the script when the user already exists."
        )
    except Exception as e:
        if "already exists" in str(e).lower() or USER_ID in str(e):
            raise e

        # User doesn't exist, create it
        user = zep_client.user.add(
            user_id=USER_ID,
            email=USER_EMAIL,
            first_name=USER_FIRST_NAME,
            last_name=USER_LAST_NAME,
        )
        print(f"✅ User {USER_ID} created successfully")
        return user


def load_conversations_from_json():
    """Load conversation data from JSON file."""
    conversations_file = "data/conversations.json"

    if not os.path.exists(conversations_file):
        raise FileNotFoundError(f"Conversations file not found: {conversations_file}")

    with open(conversations_file, 'r') as f:
        conversations = json.load(f)

    print(f"✅ Loaded {len(conversations)} conversation(s) from {conversations_file}")
    return conversations


def add_conversations_to_zep(zep_client, conversations):
    """
    Add conversations to Zep as separate threads.
    Polls the last message to ensure all processing is complete.
    """
    all_episode_uuids = []
    total_messages = 0

    # Count total messages
    for conversation in conversations:
        total_messages += len(conversation.get("messages", []))

    print(f"\n📊 Total messages to add: {total_messages}")

    # Calculate estimated processing time
    min_time_seconds = 5 * total_messages
    max_time_seconds = 20 * total_messages
    min_time_minutes = min_time_seconds / 60
    max_time_minutes = max_time_seconds / 60

    print(f"⏱️  Estimated graph processing time: {min_time_minutes:.1f} - {max_time_minutes:.1f} minutes")
    print(f"   (Processing time is {5}-{20} seconds per message)\n")

    # Process each conversation as a separate thread
    for idx, conversation in enumerate(conversations, 1):
        conversation_id = conversation.get("conversation_id", f"conv_{idx}")
        messages_data = conversation.get("messages", [])

        if not messages_data:
            print(f"⚠️  Skipping conversation {conversation_id} - no messages found")
            continue

        try:
            # Create thread for this conversation
            thread_id = f"{USER_ID}_{conversation_id}"
            zep_client.thread.create(
                thread_id=thread_id,
                user_id=USER_ID
            )
            print(f"📝 Created thread: {thread_id}")

            # Convert messages to Zep Message objects
            zep_messages = []
            for msg in messages_data:
                zep_message = Message(
                    role=msg["role"],
                    content=msg["content"],
                    created_at=msg.get("timestamp")  # Optional timestamp
                )
                zep_messages.append(zep_message)

            # Add all messages to the thread
            response = zep_client.thread.add_messages(
                thread_id=thread_id,
                messages=zep_messages
            )

            # Collect message UUIDs (these are episode IDs)
            if hasattr(response, 'message_uuids') and response.message_uuids:
                all_episode_uuids.extend(response.message_uuids)
                print(f"✅ Added {len(zep_messages)} messages to thread {thread_id} (got {len(response.message_uuids)} episode UUIDs)")
            else:
                print(f"✅ Added {len(zep_messages)} messages to thread {thread_id}")

        except Exception as e:
            print(f"❌ Error processing conversation {conversation_id}: {e}")
            continue

    # Poll the last episode to ensure all processing is complete
    if all_episode_uuids:
        last_episode_uuid = all_episode_uuids[-1]
        print(f"\n⏳ Polling last episode ({last_episode_uuid}) to ensure all messages are processed...")
        print("   (Episodes are processed in order, so when the last one is done, all are done)")

        poll_count = 0

        while True:
            try:
                episode = zep_client.graph.episode.get(uuid_=last_episode_uuid)
                if episode.processed:
                    print(f"✅ All episodes processed successfully! (Polled {poll_count} times)")
                    break

                # Print progress every 6 polls (30 seconds)
                if poll_count % 6 == 0 and poll_count > 0:
                    elapsed_minutes = (poll_count * 5) / 60
                    print(f"   Still processing... ({elapsed_minutes:.1f} minutes elapsed)")

                time.sleep(5)
                poll_count += 1

            except Exception as e:
                print(f"❌ Error polling episode: {e}")
                break
    else:
        print("⚠️  No episodes were created")


def main():
    # Load environment variables
    load_dotenv()

    # Validate environment variable
    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("❌ Error: Missing ZEP_API_KEY environment variable")
        print("   Please create a .env file with your ZEP_API_KEY")
        exit(1)

    # Initialize Zep client
    zep_client = Zep(api_key=api_key)

    print("=" * 80)
    print("ZEP INGESTION SCRIPT - Evaluation Test Harness")
    print("=" * 80)
    print(f"\n🔑 User ID: {USER_ID}\n")

    try:
        # Step 1: Create user (will error if already exists)
        create_user(zep_client)

        # Step 2: Load conversations from JSON
        conversations = load_conversations_from_json()

        # Step 3: Add conversations to Zep and poll for completion
        add_conversations_to_zep(zep_client, conversations)

        print("\n" + "=" * 80)
        print("INGESTION COMPLETE ✅")
        print("=" * 80)
        print(f"\nUser {USER_ID} is ready for evaluation testing!")
        print("Next step: Run zep_evaluate.py to perform evaluation")

    except Exception as e:
        print(f"\n❌ Script failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
