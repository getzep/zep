import os
import json
import glob
import uuid
import asyncio
import argparse
from time import time
from datetime import datetime
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

from eval_config.constants import (
    POLL_INTERVAL,
    POLL_TIMEOUT,
)
from retry import retry_with_backoff
from checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint


# Import ontology module (user only)
try:
    from eval_config.ontology import set_custom_ontology, ENTITY_TYPES, EDGE_TYPES

    CUSTOM_ONTOLOGY_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_ONTOLOGY_AVAILABLE = False
    ENTITY_TYPES = []
    EDGE_TYPES = []

# Import custom instructions module (user only)
try:
    from eval_config.custom_instructions import set_custom_instructions
    from eval_config.custom_instructions import INSTRUCTION_NAMES as CUSTOM_INSTRUCTION_NAMES

    CUSTOM_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_INSTRUCTIONS_AVAILABLE = False
    CUSTOM_INSTRUCTION_NAMES = []

# Import user summary instructions module
try:
    from eval_config.user_summary_instructions import set_user_summary_instructions
    from eval_config.user_summary_instructions import INSTRUCTION_NAMES as USER_SUMMARY_INSTRUCTION_NAMES

    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = False
    USER_SUMMARY_INSTRUCTION_NAMES = []


CHECKPOINT_DIR = "runs/checkpoints"


def checkpoint_path_for_run(run_number: int) -> str:
    """Return the checkpoint file path for a given user run."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"users_run_{run_number}.json")


# ============================================================================
# Data Loading
# ============================================================================


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


# ============================================================================
# User Creation
# ============================================================================


async def create_user(
    zep_client,
    user_definition,
    disable_default_ontology=False,
    use_custom_instructions=False,
    use_user_summary_instructions=False,
):
    """
    Create a new user with a randomized ID suffix to make ingestion idempotent.
    Applies custom ontology, custom instructions, and/or user summary instructions
    to the user before returning.

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

    # Apply custom instructions to this user BEFORE ingesting data
    if use_custom_instructions:
        print(f"Setting custom instructions for user: {user_id}")
        try:
            await set_custom_instructions(zep_client, user_ids=[user_id])
            print("✓ Custom instructions applied successfully")
        except Exception as e:
            print(f"Error setting custom instructions: {e}")
            raise

    # Apply user summary instructions to this user BEFORE ingesting data
    if use_user_summary_instructions:
        print(f"Setting user summary instructions for user: {user_id}")
        try:
            await set_user_summary_instructions(zep_client, user_ids=[user_id])
            print("✓ User summary instructions applied successfully")
        except Exception as e:
            print(f"Error setting user summary instructions: {e}")
            raise

    return user_id, base_user_id, random_suffix


# ============================================================================
# Ingestion: Conversations
# ============================================================================


async def add_conversations_to_zep(
    zep_client: AsyncZep,
    user_id: str,
    conversations: list[dict],
    suffix: str,
    user_name: str | None = None,
) -> tuple[list[str], list[str]]:
    """
    Add conversations to Zep as separate threads.
    Returns tuple of (thread_ids, task_ids) where task_ids can be polled
    via client.task.get() to check processing completion.
    """
    total_messages = 0
    thread_ids = []
    task_ids = []

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
                    name=user_name if msg["role"] == "user" and user_name else None,
                )
                zep_messages.append(zep_message)

            # Batch in groups of 30 messages
            batch_size = 30
            total_added = 0
            for i in range(0, len(zep_messages), batch_size):
                batch = zep_messages[i : i + batch_size]
                batch_label = f"batch {i // batch_size + 1} for thread {thread_id}"
                response = await retry_with_backoff(
                    zep_client.thread.add_messages,
                    thread_id=thread_id,
                    messages=batch,
                    description=batch_label,
                )
                total_added += len(batch)

                # Capture task_id for polling processing completion
                if response and hasattr(response, "task_id") and response.task_id:
                    task_ids.append(response.task_id)

            print(f"✓ Added {total_added} messages to thread {thread_id}")

        except Exception as e:
            print(f"Error processing conversation {conversation_id}: {e}")
            continue

    return thread_ids, task_ids


# ============================================================================
# Ingestion: Telemetry
# ============================================================================


async def add_telemetry_to_zep(
    zep_client: AsyncZep, user_id: str, telemetry_data: list[dict]
) -> list[str]:
    """
    Add telemetry data to Zep using graph.add.
    Returns list of episode UUIDs.
    """
    if not telemetry_data:
        return []

    print(f"\nAdding {len(telemetry_data)} telemetry file(s) for {user_id}")

    total_added = 0
    episode_uuids = []
    for idx, telemetry in enumerate(telemetry_data):
        try:
            episode = await retry_with_backoff(
                zep_client.graph.add,
                user_id=user_id,
                type="json",
                data=json.dumps(telemetry),
                description=f"telemetry episode {idx + 1}/{len(telemetry_data)} for {user_id}",
            )
            total_added += 1
            episode_uuids.append(episode.uuid_)
            print(f"✓ Added telemetry episode {idx + 1}/{len(telemetry_data)}")
        except Exception as e:
            print(f"Error adding telemetry episode {idx}: {e}")
            continue

    print(f"✓ Completed adding {total_added} telemetry episodes")
    return episode_uuids


# ============================================================================
# Polling
# ============================================================================


async def poll_episode_uuids(
    zep_client: AsyncZep, episode_uuids: list[str], label: str
) -> int:
    """
    Poll individual episode UUIDs until all are processed.
    This is the correct polling approach per Zep docs: check each episode
    returned from graph.add() via graph.episode.get(uuid) until .processed
    is true. Using get_by_graph_id/get_by_user_id with lastn is unreliable
    because it may return only a subset of episodes and report completion
    prematurely.
    Returns number of processed episodes.
    """
    if not episode_uuids:
        print(f"  [{label}] No episodes to poll")
        return 0

    remaining = set(episode_uuids)
    processed_count = 0
    start = time()

    while remaining and time() - start < POLL_TIMEOUT:
        newly_processed = []
        for ep_uuid in remaining:
            try:
                episode = await zep_client.graph.episode.get(ep_uuid)
                if episode.processed:
                    newly_processed.append(ep_uuid)
            except Exception:
                pass  # Episode may not be available yet, retry next cycle

        for ep_uuid in newly_processed:
            remaining.discard(ep_uuid)
            processed_count += 1

        if not remaining:
            print(
                f"  ✓ [{label}] All {len(episode_uuids)} episodes processed"
            )
            return len(episode_uuids)

        print(
            f"  [{label}] {processed_count}/{len(episode_uuids)} episodes processed..."
        )
        await asyncio.sleep(POLL_INTERVAL)

    if remaining:
        print(
            f"  ⚠ [{label}] Polling timed out after {POLL_TIMEOUT}s "
            f"({processed_count}/{len(episode_uuids)} processed)"
        )
    return processed_count


async def poll_task_ids(
    zep_client: AsyncZep, task_ids: list[str], label: str
) -> int:
    """
    Poll task IDs until all have succeeded or failed.
    This is used for thread.add_messages operations which return task_ids
    (rather than episode UUIDs) for tracking processing status.
    Uses client.task.get(task_id=...) and checks .status for
    "succeeded" or "failed".
    Returns number of successfully completed tasks.
    """
    if not task_ids:
        print(f"  [{label}] No conversation tasks to poll")
        return 0

    remaining = set(task_ids)
    succeeded_count = 0
    failed_count = 0
    start = time()

    while remaining and time() - start < POLL_TIMEOUT:
        newly_done = []
        for task_id in remaining:
            try:
                task = await zep_client.task.get(task_id=task_id)
                if task.status == "succeeded":
                    newly_done.append(task_id)
                    succeeded_count += 1
                elif task.status == "failed":
                    newly_done.append(task_id)
                    failed_count += 1
                    error_msg = getattr(task, "error", "unknown error")
                    print(f"  ⚠ [{label}] Task {task_id} failed: {error_msg}")
            except Exception:
                pass  # Task may not be available yet, retry next cycle

        for task_id in newly_done:
            remaining.discard(task_id)

        if not remaining:
            total = len(task_ids)
            print(
                f"  ✓ [{label}] All {total} conversation tasks completed "
                f"({succeeded_count} succeeded, {failed_count} failed)"
            )
            return succeeded_count

        done = succeeded_count + failed_count
        print(
            f"  [{label}] {done}/{len(task_ids)} conversation tasks completed..."
        )
        await asyncio.sleep(POLL_INTERVAL)

    if remaining:
        done = succeeded_count + failed_count
        print(
            f"  ⚠ [{label}] Polling timed out after {POLL_TIMEOUT}s "
            f"({done}/{len(task_ids)} completed)"
        )
    return succeeded_count


# ============================================================================
# Per-User Ingestion Pipeline
# ============================================================================


async def ingest_user(
    zep_client: AsyncZep,
    user_def: dict,
    use_custom_ontology: bool,
    use_custom_instructions: bool = False,
    use_user_summary_instructions: bool = False,
) -> dict:
    """
    Run the full ingestion pipeline for a single user:
    create user, load data, add conversations + telemetry.
    Returns a manifest data dict for this user.
    """
    base_user_id = user_def["user_id"]
    print("=" * 80)
    print(
        f"Processing user: {user_def['first_name']} {user_def.get('last_name', '')}"
    )
    print("=" * 80)

    # Create user
    actual_user_id, base_user_id, suffix = await create_user(
        zep_client,
        user_def,
        disable_default_ontology=use_custom_ontology,
        use_custom_instructions=use_custom_instructions,
        use_user_summary_instructions=use_user_summary_instructions,
    )

    # Load data
    conversations = load_conversations_for_user(base_user_id)
    telemetry_data = load_telemetry_for_user(base_user_id)

    # Add conversations
    thread_ids = []
    conversation_task_ids = []
    if conversations:
        first = user_def.get("first_name", "")
        last = user_def.get("last_name", "")
        full_name = f"{first} {last}".strip() or None

        thread_ids, conversation_task_ids = await add_conversations_to_zep(
            zep_client,
            actual_user_id,
            conversations,
            suffix,
            user_name=full_name,
        )

    # Add telemetry
    episode_uuids = []
    if telemetry_data:
        episode_uuids = await add_telemetry_to_zep(
            zep_client, actual_user_id, telemetry_data
        )

    print(f"✓ Data submitted for user {actual_user_id}\n")

    return {
        "base_user_id": base_user_id,
        "zep_user_id": actual_user_id,
        "first_name": user_def["first_name"],
        "last_name": user_def.get("last_name"),
        "thread_ids": thread_ids,
        "conversation_task_ids": conversation_task_ids,
        "episode_uuids": episode_uuids,
        "num_conversations": len(conversations),
        "num_telemetry_files": len(telemetry_data) if telemetry_data else 0,
    }


# ============================================================================
# Run Manifest
# ============================================================================


def get_next_run_number():
    """
    Get the next run number by checking existing run directories.
    """
    os.makedirs("runs/users", exist_ok=True)
    existing_runs = glob.glob("runs/users/*")

    if not existing_runs:
        return 1

    # Extract run numbers from directory names (format: runs/users/1_timestamp)
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


def write_run_manifest(
    run_number,
    run_data,
    use_custom_ontology=False,
    use_custom_instructions=False,
    use_user_summary_instructions=False,
):
    """
    Write a manifest file for the current run.
    Format: runs/users/{number}_{ISO8601_timestamp}/
    """
    timestamp = datetime.now().isoformat()
    # Use ISO 8601 format with basic format (no colons for filesystem compatibility)
    timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = f"runs/users/{run_number}_{timestamp_str}"
    os.makedirs(run_dir, exist_ok=True)

    manifest = {
        "run_number": run_number,
        "type": "users",
        "timestamp": timestamp,
        "ontology": {
            "type": (
                "custom" if use_custom_ontology else "default_zep"
            ),
            "default_ontology_disabled": use_custom_ontology,
            "custom_entity_types": ENTITY_TYPES if use_custom_ontology else [],
            "custom_edge_types": EDGE_TYPES if use_custom_ontology else [],
        },
        "custom_instructions": {
            "enabled": use_custom_instructions,
            "instruction_names": CUSTOM_INSTRUCTION_NAMES if use_custom_instructions else [],
        },
        "user_summary_instructions": {
            "enabled": use_user_summary_instructions,
            "instruction_names": USER_SUMMARY_INSTRUCTION_NAMES if use_user_summary_instructions else [],
        },
        "users": run_data,
    }

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, indent=2, fp=f)

    print(f"\nRun manifest written to: {manifest_path}")
    return run_dir


# ============================================================================
# CLI
# ============================================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zep Eval Harness — User Graph Ingestion Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_ingest_users.py                                   # Ingest all users, poll until done
  uv run zep_ingest_users.py --no-poll                         # Ingest all, don't wait
  uv run zep_ingest_users.py --graphs zep_eval_test_user_001   # Ingest one user only
  uv run zep_ingest_users.py --custom-ontology                 # Use custom ontology
  uv run zep_ingest_users.py --custom-instructions             # Use custom instructions
  uv run zep_ingest_users.py --user-summary-instructions       # Use user summary instructions
  uv run zep_ingest_users.py --resume runs/checkpoints/users_run_3.json  # Resume from checkpoint
""",
    )

    parser.add_argument(
        "--custom-ontology",
        action="store_true",
        help="Use custom ontology for user graphs instead of Zep defaults",
    )
    parser.add_argument(
        "--custom-instructions",
        action="store_true",
        help="Use custom instructions for user graph extraction (domain context)",
    )
    parser.add_argument(
        "--user-summary-instructions",
        action="store_true",
        help="Use custom user summary instructions (customize user node summaries)",
    )
    parser.add_argument(
        "--no-poll",
        action="store_true",
        help="Don't wait for episode processing to complete (default: poll)",
    )
    parser.add_argument(
        "--graphs",
        type=str,
        default=None,
        help=(
            "Comma-separated list of user base IDs to ingest "
            "(e.g. zep_eval_test_user_001). Default: all"
        ),
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Path to a checkpoint file to resume a previously interrupted ingestion",
    )
    return parser.parse_args()


# ============================================================================
# Main
# ============================================================================


async def main():
    load_dotenv()
    args = parse_args()

    # Handle resume mode
    checkpoint_data = None
    if args.resume:
        if not os.path.exists(args.resume):
            print(f"Error: Checkpoint file not found: {args.resume}")
            exit(1)
        checkpoint_data = load_checkpoint(args.resume)
        print(f"✓ Loaded checkpoint from: {args.resume}")
        completed_user_ids = {u["base_user_id"] for u in checkpoint_data.get("completed_users", [])}
        print(f"  Completed users: {len(completed_user_ids)}")

        # Restore config from checkpoint
        config = checkpoint_data.get("config", {})
        args.custom_ontology = config.get("custom_ontology", False)
        args.custom_instructions = config.get("custom_instructions", False)
        args.user_summary_instructions = config.get("user_summary_instructions", False)

    # Validate user graph flags
    if args.custom_ontology:
        if not CUSTOM_ONTOLOGY_AVAILABLE:
            print("Error: Custom ontology module could not be loaded")
            print("   Check that ontology.py exists and is valid")
            exit(1)

    if args.custom_instructions:
        if not CUSTOM_INSTRUCTIONS_AVAILABLE:
            print("Error: Custom instructions module could not be loaded")
            print("   Check that custom_instructions.py exists and is valid")
            exit(1)

    if args.user_summary_instructions:
        if not USER_SUMMARY_INSTRUCTIONS_AVAILABLE:
            print("Error: User summary instructions module could not be loaded")
            print("   Check that user_summary_instructions.py exists and is valid")
            exit(1)

    # Validate environment variables
    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        print("   Please create a .env file with your ZEP_API_KEY")
        exit(1)

    # Initialize client
    zep_client = AsyncZep(api_key=api_key)

    # Determine which users to ingest
    should_poll = not args.no_poll

    user_definitions = load_user_definitions()
    if not user_definitions:
        print("Error: No users found in data/users.json")
        exit(1)

    selected_user_defs = user_definitions

    if args.graphs:
        selected = set(g.strip() for g in args.graphs.split(","))
        selected_user_defs = [
            u for u in user_definitions if u["user_id"] in selected
        ]
        unknown = selected - {u["user_id"] for u in user_definitions}
        if unknown:
            print(f"Warning: Unknown user IDs (not in users.json): {unknown}")

    # Filter out already-completed users when resuming
    if checkpoint_data:
        completed_user_ids = {u["base_user_id"] for u in checkpoint_data.get("completed_users", [])}
        remaining_user_defs = [
            u for u in selected_user_defs if u["user_id"] not in completed_user_ids
        ]
        print(f"  Skipping {len(selected_user_defs) - len(remaining_user_defs)} already-completed users")
        selected_user_defs = remaining_user_defs

    # Print header
    print("=" * 80)
    print("ZEP USER GRAPH INGESTION" + (" (RESUMING)" if checkpoint_data else ""))
    print("=" * 80)
    if args.custom_ontology:
        print("  Ontology: Custom (default ontology suppressed)")
    else:
        print("  Ontology: Default Zep ontology")
    print(f"  Custom instructions: {'enabled' if args.custom_instructions else 'disabled'}")
    print(f"  User summary instructions: {'enabled' if args.user_summary_instructions else 'disabled'}")
    print(f"  Polling: {'enabled' if should_poll else 'disabled'}")
    print(f"  Users: {len(selected_user_defs)}")
    print("=" * 80)

    try:
        # Use checkpoint run_number or get a new one
        if checkpoint_data:
            run_number = checkpoint_data.get("run_number", get_next_run_number())
            previously_completed = list(checkpoint_data.get("completed_users", []))
        else:
            run_number = get_next_run_number()
            previously_completed = []

        cp_path = checkpoint_path_for_run(run_number)
        checkpoint_lock = asyncio.Lock()

        # Shared mutable state for checkpoint updates
        completed_users = list(previously_completed)

        async def ingest_user_with_checkpoint(user_def):
            """Wrapper that updates the checkpoint after each user completes."""
            result = await ingest_user(
                zep_client,
                user_def,
                args.custom_ontology,
                use_custom_instructions=args.custom_instructions,
                use_user_summary_instructions=args.user_summary_instructions,
            )
            async with checkpoint_lock:
                completed_users.append(result)
                save_checkpoint(cp_path, {
                    "run_number": run_number,
                    "config": {
                        "custom_ontology": args.custom_ontology,
                        "custom_instructions": args.custom_instructions,
                        "user_summary_instructions": args.user_summary_instructions,
                    },
                    "completed_users": completed_users,
                })
            return result

        print(f"\n{'Resuming' if checkpoint_data else 'Starting'} run #{run_number}\n")

        # Launch all ingestion tasks in parallel
        user_tasks = [
            ingest_user_with_checkpoint(user_def)
            for user_def in selected_user_defs
        ]

        # Gather all tasks concurrently (return_exceptions so one failure
        # doesn't cancel the rest)
        if user_tasks:
            raw_user_results = await asyncio.gather(
                *user_tasks, return_exceptions=True
            )
        else:
            raw_user_results = []

        # Separate successful results from failures
        run_data = list(previously_completed)
        failed_users = []
        for i, result in enumerate(raw_user_results):
            if isinstance(result, Exception):
                base_id = selected_user_defs[i]["user_id"]
                print(f"⚠ Ingestion failed for {base_id}: {result}")
                failed_users.append(base_id)
            else:
                run_data.append(result)

        if failed_users:
            print(f"\n⚠ {len(failed_users)} user(s) failed. Checkpoint saved to: {cp_path}")
            print(f"  Resume with: uv run zep_ingest_users.py --resume {cp_path}")
        else:
            # All succeeded — clean up checkpoint
            delete_checkpoint(cp_path)

        # Write run manifest
        run_dir = write_run_manifest(
            run_number,
            run_data,
            use_custom_ontology=args.custom_ontology,
            use_custom_instructions=args.custom_instructions,
            use_user_summary_instructions=args.user_summary_instructions,
        )

        print("\n" + "=" * 80)
        print("INGESTION COMPLETE")
        print("=" * 80)
        print(f"\nRun #{run_number}")
        print(f"Manifest: {run_dir}/manifest.json")
        print(f"Users: {len(run_data)} succeeded, {len(failed_users)} failed")

        # Poll for processing completion
        if should_poll:
            print("\n" + "=" * 80)
            print("POLLING FOR PROCESSING COMPLETION")
            print("=" * 80)
            print(
                f"Checking every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT}s)\n"
            )

            poll_tasks = []

            # Poll each user's conversation task IDs (from thread.add_messages)
            for user_data in run_data:
                task_ids = user_data.get("conversation_task_ids", [])
                if task_ids:
                    label = f"{user_data['zep_user_id']} conversations"
                    poll_tasks.append(
                        poll_task_ids(zep_client, task_ids, label)
                    )

            # Poll each user's telemetry episodes (from graph.add)
            for user_data in run_data:
                uuids = user_data.get("episode_uuids", [])
                if uuids:
                    label = f"{user_data['zep_user_id']} telemetry"
                    poll_tasks.append(
                        poll_episode_uuids(zep_client, uuids, label)
                    )

            if poll_tasks:
                await asyncio.gather(*poll_tasks)
                print("\n✓ All user graphs finished processing")
            else:
                print("No graphs to poll.")

            print(
                f"\nYou can now run: uv run zep_evaluate.py --user-run {run_number}"
            )
        else:
            print("\nGraph processing happens asynchronously and may take several minutes.")
            print(
                f"You can run zep_evaluate.py with --user-run {run_number} once processing is complete."
            )

    except Exception as e:
        print(f"\nScript failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
