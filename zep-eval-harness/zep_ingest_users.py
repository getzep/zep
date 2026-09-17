import os
import json
import glob
import shutil
import asyncio
import argparse
from time import time
from datetime import datetime
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud.types import BatchItemInput

from config.constants import (
    POLL_INTERVAL,
    POLL_TIMEOUT_PER_EPISODE,
)
from retry import retry_with_backoff
from checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint


# Import ontology module (user only)
try:
    from config.user_ingestion_config.ontology import (
        set_custom_ontology,
        ENTITY_TYPES,
        EDGE_TYPES,
    )

    CUSTOM_ONTOLOGY_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_ONTOLOGY_AVAILABLE = False
    ENTITY_TYPES = []
    EDGE_TYPES = []

# Import custom instructions module (user only)
try:
    from config.user_ingestion_config.custom_instructions import set_custom_instructions
    from config.user_ingestion_config.custom_instructions import (
        INSTRUCTION_NAMES as CUSTOM_INSTRUCTION_NAMES,
    )

    CUSTOM_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_INSTRUCTIONS_AVAILABLE = False
    CUSTOM_INSTRUCTION_NAMES = []

# Import user summary instructions module
try:
    from config.user_ingestion_config.user_summary_instructions import (
        set_user_summary_instructions,
    )
    from config.user_ingestion_config.user_summary_instructions import (
        INSTRUCTION_NAMES as USER_SUMMARY_INSTRUCTION_NAMES,
    )

    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = False
    USER_SUMMARY_INSTRUCTION_NAMES = []


CHECKPOINT_DIR = "runs/checkpoints"

# The v4 Batch API accepts up to 350 items in one batch.add_items call.
BATCH_ITEM_LIMIT = 350


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
    If all messages across all conversations have timestamps, sorts
    conversations by earliest first message (oldest first). Otherwise
    falls back to glob ordering.
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

    # Sort by timestamp if every message has one
    all_have_timestamps = all(
        msg.get("timestamp")
        for conv in conversations
        for msg in conv.get("messages", [])
    )
    if all_have_timestamps and len(conversations) > 1:
        conversations.sort(key=lambda c: c["messages"][0]["timestamp"])
        print(
            f"✓ Loaded {len(conversations)} conversation(s) for user {user_id} (sorted by timestamp)"
        )
    else:
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
    Create a new user. v4 gives each user a server-generated UUID, so every run
    creates an independent user and no identifier suffix is necessary.
    Applies custom ontology, custom instructions, and/or user summary instructions
    to the user before returning.

    Returns tuple of (user_uuid, user_graph_uuid).
    """
    base_user_id = user_definition["user_id"]

    user = await zep_client.user.create(
        first_name=user_definition["first_name"],
        last_name=user_definition.get("last_name"),
        email=user_definition.get("email"),
        disable_default_ontology=disable_default_ontology,
    )
    user_uuid = user.uuid_
    graph_uuid = user.graph_uuid

    print(f"✓ User {user_uuid} created successfully")
    print(
        f"   Name: {user_definition['first_name']} {user_definition.get('last_name', '')}"
    )
    print(f"   Base ID: {base_user_id}")
    print(f"   Graph UUID: {graph_uuid}")
    if disable_default_ontology:
        print("   Default ontology: DISABLED")

    # Apply custom ontology to this user graph BEFORE ingesting data
    if disable_default_ontology:
        print("\nSetting up custom ontology...")
        print(f"Applying to graph: {graph_uuid}")
        try:
            await set_custom_ontology(zep_client, graph_uuids=[graph_uuid])
            print("✓ Custom ontology applied successfully\n")
        except Exception as e:
            print(f"Error setting ontology: {e}")
            raise

    # Apply custom instructions to this user graph BEFORE ingesting data
    if use_custom_instructions:
        print(f"Setting custom instructions for graph: {graph_uuid}")
        try:
            await set_custom_instructions(zep_client, graph_uuids=[graph_uuid])
            print("✓ Custom instructions applied successfully")
        except Exception as e:
            print(f"Error setting custom instructions: {e}")
            raise

    # Apply user summary instructions to this user BEFORE ingesting data
    if use_user_summary_instructions:
        print(f"Setting user summary instructions for user: {user_uuid}")
        try:
            await set_user_summary_instructions(zep_client, user_uuids=[user_uuid])
            print("✓ User summary instructions applied successfully")
        except Exception as e:
            print(f"Error setting user summary instructions: {e}")
            raise

    return user_uuid, graph_uuid


# ============================================================================
# Ingestion: Batch API
# ============================================================================


async def submit_batch(
    zep_client: AsyncZep, items: list[BatchItemInput], label: str
) -> str | None:
    """
    Send one group of items through the v4 Batch API: create, add items,
    and process. Returns the UUID of the processing task.
    """
    batch = await retry_with_backoff(
        zep_client.batch.create,
        metadata={"description": label},
        description=f"create batch for {label}",
    )
    await retry_with_backoff(
        zep_client.batch.add_items,
        batch.uuid_,
        items=items,
        description=f"add items to {label}",
    )
    result = await retry_with_backoff(
        zep_client.batch.process,
        batch.uuid_,
        description=f"process {label}",
    )
    return result.task.uuid_ if result.task else None


# ============================================================================
# Ingestion: Conversations
# ============================================================================


async def add_conversations_to_zep(
    zep_client: AsyncZep,
    user_uuid: str,
    conversations: list[dict],
    user_name: str | None = None,
) -> tuple[list[str], list[tuple[str, int]]]:
    """
    Add conversations to Zep as separate threads through the Batch API.
    Returns tuple of (thread_uuids, tasks) where tasks is a list of
    (task_uuid, num_episodes) tuples for sequential polling.
    """
    total_messages = 0
    thread_uuids = []
    tasks = []

    # Count total messages
    for conversation in conversations:
        total_messages += len(conversation.get("messages", []))

    print(f"\nTotal messages to add for user {user_uuid}: {total_messages}")

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
            # v4 addresses a thread by UUID, so the thread needs no identifier
            thread = await zep_client.thread.create(user_uuid=user_uuid)
            thread_uuid = thread.uuid_
            print(f"Created thread: {thread_uuid}")
            thread_uuids.append(thread_uuid)

            # Convert messages to batch items for this thread
            items = [
                BatchItemInput(
                    type="thread_message",
                    thread_uuid=thread_uuid,
                    role=msg["role"],
                    content=msg["content"],
                    name=user_name if msg["role"] == "user" and user_name else None,
                )
                for msg in messages_data
            ]

            total_added = 0
            for i in range(0, len(items), BATCH_ITEM_LIMIT):
                group = items[i : i + BATCH_ITEM_LIMIT]
                batch_label = (
                    f"batch {i // BATCH_ITEM_LIMIT + 1} for thread {thread_uuid}"
                )
                task_uuid = await submit_batch(zep_client, group, batch_label)
                total_added += len(group)
                if task_uuid:
                    tasks.append((task_uuid, len(group)))

            print(f"✓ Added {total_added} messages to thread {thread_uuid}")

        except Exception as e:
            print(f"Error processing conversation {conversation_id}: {e}")
            continue

    return thread_uuids, tasks


# ============================================================================
# Ingestion: Telemetry
# ============================================================================


async def add_telemetry_to_zep(
    zep_client: AsyncZep, graph_uuid: str, telemetry_data: list[dict]
) -> list[tuple[str, int]]:
    """
    Add telemetry data to the user graph through the Batch API.
    Returns list of (task_uuid, num_episodes) tuples for sequential polling.
    """
    if not telemetry_data:
        return []

    print(f"\nAdding {len(telemetry_data)} telemetry file(s) to graph {graph_uuid}")

    total_added = 0
    tasks = []

    for i in range(0, len(telemetry_data), BATCH_ITEM_LIMIT):
        batch = telemetry_data[i : i + BATCH_ITEM_LIMIT]
        items = [
            BatchItemInput(
                type="graph_episode",
                graph_uuid=graph_uuid,
                data=json.dumps(t),
                data_type="json",
            )
            for t in batch
        ]
        batch_num = i // BATCH_ITEM_LIMIT + 1
        batch_label = (
            f"telemetry batch {batch_num} ({len(batch)} episodes) for {graph_uuid}"
        )

        try:
            task_uuid = await submit_batch(zep_client, items, batch_label)
            total_added += len(batch)
            if task_uuid:
                tasks.append((task_uuid, len(batch)))

            print(f"✓ Added telemetry batch {batch_num} ({len(batch)} episodes)")
        except Exception as e:
            print(f"Error adding telemetry batch {batch_num}: {e}")
            continue

    print(f"✓ Completed adding {total_added} telemetry episodes")
    return tasks


# ============================================================================
# Polling
# ============================================================================


async def poll_task_uuids(
    zep_client: AsyncZep, tasks: list[tuple[str, int]], label: str
) -> dict:
    """
    Poll tasks sequentially until all are completed.
    Each task gets a timeout proportional to its episode count:
    timeout = num_episodes * POLL_TIMEOUT_PER_EPISODE.
    The clock for each task starts only after the previous task completes.
    Returns dict with succeeded/failed counts, timing, and episode stats.
    """
    if not tasks:
        print(f"  [{label}] No tasks to poll")
        return {
            "succeeded": 0,
            "failed": 0,
            "total_episodes": 0,
            "elapsed_seconds": 0,
            "avg_seconds_per_episode": 0,
        }

    total_tasks = len(tasks)
    total_episodes = sum(n for _, n in tasks)
    succeeded_count = 0
    failed_count = 0
    poll_start = time()

    for task_uuid, num_episodes in tasks:
        task_timeout = num_episodes * POLL_TIMEOUT_PER_EPISODE
        start = time()

        while time() - start < task_timeout:
            try:
                task = await zep_client.task.get(task_uuid)
                if task.status == "succeeded":
                    succeeded_count += 1
                    done = succeeded_count + failed_count
                    print(f"  ✓ [{label}] Task {done}/{total_tasks} completed")
                    break
                elif task.status == "failed" or task.error:
                    failed_count += 1
                    done = succeeded_count + failed_count
                    error_msg = task.error.message if task.error else "unknown error"
                    print(
                        f"  ⚠ [{label}] Task {done}/{total_tasks} FAILED: {error_msg}"
                    )
                    break
            except Exception:
                pass  # Task may not be available yet
            await asyncio.sleep(POLL_INTERVAL)
        else:
            elapsed = time() - poll_start
            avg = elapsed / total_episodes if total_episodes else 0
            print(
                f"  ⚠ [{label}] Task {task_uuid} ({num_episodes} episodes) "
                f"timed out after {task_timeout}s"
            )
            done = succeeded_count + failed_count
            print(f"  ⚠ [{label}] Stopping poll — {done}/{total_tasks} done")
            return {
                "succeeded": succeeded_count,
                "failed": failed_count,
                "total_episodes": total_episodes,
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_episode": round(avg, 1),
            }

    elapsed = time() - poll_start
    avg = elapsed / total_episodes if total_episodes else 0

    if failed_count:
        print(
            f"  ⚠ [{label}] {succeeded_count}/{total_tasks} succeeded, "
            f"{failed_count} failed"
        )
    else:
        print(f"  ✓ [{label}] All {total_tasks} tasks succeeded")

    return {
        "succeeded": succeeded_count,
        "failed": failed_count,
        "total_episodes": total_episodes,
        "elapsed_seconds": round(elapsed, 1),
        "avg_seconds_per_episode": round(avg, 1),
    }


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
    print(f"Processing user: {user_def['first_name']} {user_def.get('last_name', '')}")
    print("=" * 80)

    # Create user
    user_uuid, graph_uuid = await create_user(
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
    thread_uuids: list[str] = []
    conversation_tasks: list[tuple[str, int]] = []
    if conversations:
        first = user_def.get("first_name", "")
        last = user_def.get("last_name", "")
        full_name = f"{first} {last}".strip() or None

        thread_uuids, conversation_tasks = await add_conversations_to_zep(
            zep_client,
            user_uuid,
            conversations,
            user_name=full_name,
        )

    # Add telemetry
    telemetry_tasks = []
    if telemetry_data:
        telemetry_tasks = await add_telemetry_to_zep(
            zep_client, graph_uuid, telemetry_data
        )

    print(f"✓ Data submitted for user {user_uuid}\n")

    return {
        "base_user_id": base_user_id,
        "zep_user_uuid": user_uuid,
        "zep_user_graph_uuid": graph_uuid,
        "first_name": user_def["first_name"],
        "last_name": user_def.get("last_name"),
        "thread_uuids": thread_uuids,
        "conversation_tasks": conversation_tasks,
        "telemetry_tasks": telemetry_tasks,
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

    # Snapshot the user ingestion config used for this run
    snapshot_dir = os.path.join(run_dir, "user_ingestion_config_snapshot")
    shutil.copytree(
        "config/user_ingestion_config",
        snapshot_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    manifest = {
        "run_number": run_number,
        "type": "users",
        "timestamp": timestamp,
        "ontology": {
            "type": ("custom" if use_custom_ontology else "default_zep"),
            "default_ontology_disabled": use_custom_ontology,
            "custom_entity_types": ENTITY_TYPES if use_custom_ontology else [],
            "custom_edge_types": EDGE_TYPES if use_custom_ontology else [],
        },
        "custom_instructions": {
            "enabled": use_custom_instructions,
            "instruction_names": CUSTOM_INSTRUCTION_NAMES
            if use_custom_instructions
            else [],
        },
        "user_summary_instructions": {
            "enabled": use_user_summary_instructions,
            "instruction_names": USER_SUMMARY_INSTRUCTION_NAMES
            if use_user_summary_instructions
            else [],
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
        completed_user_ids = {
            u["base_user_id"] for u in checkpoint_data.get("completed_users", [])
        }
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
        selected_user_defs = [u for u in user_definitions if u["user_id"] in selected]
        unknown = selected - {u["user_id"] for u in user_definitions}
        if unknown:
            print(f"Warning: Unknown user IDs (not in users.json): {unknown}")

    # Filter out already-completed users when resuming
    if checkpoint_data:
        completed_user_ids = {
            u["base_user_id"] for u in checkpoint_data.get("completed_users", [])
        }
        remaining_user_defs = [
            u for u in selected_user_defs if u["user_id"] not in completed_user_ids
        ]
        print(
            f"  Skipping {len(selected_user_defs) - len(remaining_user_defs)} already-completed users"
        )
        selected_user_defs = remaining_user_defs

    # Print header
    print("=" * 80)
    print("ZEP USER GRAPH INGESTION" + (" (RESUMING)" if checkpoint_data else ""))
    print("=" * 80)
    if args.custom_ontology:
        print("  Ontology: Custom (default ontology suppressed)")
    else:
        print("  Ontology: Default Zep ontology")
    print(
        f"  Custom instructions: {'enabled' if args.custom_instructions else 'disabled'}"
    )
    print(
        f"  User summary instructions: {'enabled' if args.user_summary_instructions else 'disabled'}"
    )
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
                save_checkpoint(
                    cp_path,
                    {
                        "run_number": run_number,
                        "config": {
                            "custom_ontology": args.custom_ontology,
                            "custom_instructions": args.custom_instructions,
                            "user_summary_instructions": args.user_summary_instructions,
                        },
                        "completed_users": completed_users,
                    },
                )
            return result

        print(f"\n{'Resuming' if checkpoint_data else 'Starting'} run #{run_number}\n")

        # Launch all ingestion tasks in parallel
        user_tasks = [
            ingest_user_with_checkpoint(user_def) for user_def in selected_user_defs
        ]

        # Gather all tasks concurrently (return_exceptions so one failure
        # doesn't cancel the rest)
        if user_tasks:
            raw_user_results = await asyncio.gather(*user_tasks, return_exceptions=True)
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
            print(
                f"\n⚠ {len(failed_users)} user(s) failed. Checkpoint saved to: {cp_path}"
            )
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
                f"Checking every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT_PER_EPISODE}s per episode)\n"
            )

            async def poll_user_graph(user_data):
                """Poll all tasks for a single user graph, return per-graph timing."""
                graph_uuid = user_data["zep_user_graph_uuid"]
                conv_tasks = user_data.get("conversation_tasks", [])
                tele_tasks = user_data.get("telemetry_tasks", [])
                if not conv_tasks and not tele_tasks:
                    return None
                graph_start = time()
                conv_result = (
                    await poll_task_uuids(
                        zep_client, conv_tasks, f"{graph_uuid} conversations"
                    )
                    if conv_tasks
                    else None
                )
                tele_result = (
                    await poll_task_uuids(
                        zep_client, tele_tasks, f"{graph_uuid} telemetry"
                    )
                    if tele_tasks
                    else None
                )
                graph_elapsed = time() - graph_start
                total_ep = (conv_result["total_episodes"] if conv_result else 0) + (
                    tele_result["total_episodes"] if tele_result else 0
                )
                succeeded = (conv_result["succeeded"] if conv_result else 0) + (
                    tele_result["succeeded"] if tele_result else 0
                )
                failed = (conv_result["failed"] if conv_result else 0) + (
                    tele_result["failed"] if tele_result else 0
                )
                avg = graph_elapsed / total_ep if total_ep else 0
                print(
                    f"  [{graph_uuid}] Graph total: {graph_elapsed:.1f}s | "
                    f"{total_ep} episodes | {avg:.1f}s avg/episode"
                )
                return {
                    "graph_uuid": graph_uuid,
                    "total_seconds": round(graph_elapsed, 1),
                    "total_episodes": total_ep,
                    "avg_seconds_per_episode": round(avg, 1),
                    "succeeded": succeeded,
                    "failed": failed,
                }

            poll_coros = [
                poll_user_graph(ud)
                for ud in run_data
                if ud.get("conversation_tasks") or ud.get("telemetry_tasks")
            ]

            if poll_coros:
                poll_start = time()
                raw_results = await asyncio.gather(*poll_coros)
                poll_elapsed = time() - poll_start

                per_graph = [r for r in raw_results if r is not None]
                total_episodes = sum(r["total_episodes"] for r in per_graph)
                avg_per_episode = poll_elapsed / total_episodes if total_episodes else 0

                print("\n✓ All user graphs finished processing")
                print(f"\n  Overall ingestion processing time: {poll_elapsed:.1f}s")
                print(f"  Total episodes: {total_episodes}")
                print(f"  Avg time per episode: {avg_per_episode:.1f}s")

                # Save timing to manifest
                timing = {
                    "total_seconds": round(poll_elapsed, 1),
                    "total_episodes": total_episodes,
                    "avg_seconds_per_episode": round(avg_per_episode, 1),
                    "per_graph": per_graph,
                }
                manifest_path = os.path.join(run_dir, "manifest.json")
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                manifest["ingestion_timing"] = timing
                with open(manifest_path, "w") as f:
                    json.dump(manifest, indent=2, fp=f)
            else:
                print("No graphs to poll.")

            print(f"\nYou can now run: uv run zep_evaluate.py --user-run {run_number}")
        else:
            print(
                "\nGraph processing happens asynchronously and may take several minutes."
            )
            print(
                f"You can run zep_evaluate.py with --user-run {run_number} once processing is complete."
            )

    except Exception as e:
        print(f"\nScript failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
