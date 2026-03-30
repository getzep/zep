import os
import sys
import json
import glob
import uuid
import asyncio
import argparse
from time import time
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message
from chonkie import RecursiveChunker, RecursiveRules, RecursiveLevel

from constants import (
    CHUNK_SIZE,
    DOCUMENT_INGEST_LIMIT,
    DOCUMENTS_GRAPH_ID,
    GEMINI_BASE_URL,
    LLM_CONTEXTUALIZATION_MODEL,
    POLL_INTERVAL,
    POLL_TIMEOUT,
)


# Import ontology module
try:
    from ontology import set_custom_ontology, ENTITY_TYPES, EDGE_TYPES

    CUSTOM_ONTOLOGY_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_ONTOLOGY_AVAILABLE = False
    ENTITY_TYPES = []
    EDGE_TYPES = []

# Import custom instructions module
try:
    from custom_instructions import set_custom_instructions
    from custom_instructions import INSTRUCTION_NAMES as CUSTOM_INSTRUCTION_NAMES

    CUSTOM_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    CUSTOM_INSTRUCTIONS_AVAILABLE = False
    CUSTOM_INSTRUCTION_NAMES = []

# Import user summary instructions module
try:
    from user_summary_instructions import set_user_summary_instructions
    from user_summary_instructions import INSTRUCTION_NAMES as USER_SUMMARY_INSTRUCTION_NAMES

    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    USER_SUMMARY_INSTRUCTIONS_AVAILABLE = False
    USER_SUMMARY_INSTRUCTION_NAMES = []


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


def load_documents() -> list[tuple[str, str]]:
    """
    Load all documents from data/documents/.
    Returns list of (filename, content) tuples.
    """
    docs_dir = "data/documents"
    if not os.path.isdir(docs_dir):
        return []

    all_files = sorted(
        f for f in glob.glob(os.path.join(docs_dir, "*")) if os.path.isfile(f)
    )

    # Apply DOCUMENT_INGEST_LIMIT (None = all)
    if DOCUMENT_INGEST_LIMIT is not None:
        selected_files = all_files[:DOCUMENT_INGEST_LIMIT]
    else:
        selected_files = all_files

    documents = []
    for file_path in selected_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if content.strip():
            documents.append((os.path.basename(file_path), content))

    if documents:
        total = len(all_files)
        if DOCUMENT_INGEST_LIMIT is not None and DOCUMENT_INGEST_LIMIT < total:
            print(f"✓ Loaded {len(documents)} of {total} document(s) from {docs_dir} (limit: {DOCUMENT_INGEST_LIMIT})")
        else:
            print(f"✓ Loaded {len(documents)} document(s) from {docs_dir}")
    return documents


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
) -> list[str]:
    """
    Add conversations to Zep as separate threads.
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
                    name=user_name if msg["role"] == "user" and user_name else None,
                )
                zep_messages.append(zep_message)

            # Batch in groups of 30 messages
            batch_size = 30
            total_added = 0
            for i in range(0, len(zep_messages), batch_size):
                batch = zep_messages[i : i + batch_size]
                await zep_client.thread.add_messages(
                    thread_id=thread_id, messages=batch
                )
                total_added += len(batch)

            print(f"✓ Added {total_added} messages to thread {thread_id}")

        except Exception as e:
            print(f"Error processing conversation {conversation_id}: {e}")
            continue

    return thread_ids


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
            episode = await zep_client.graph.add(
                user_id=user_id,
                type="json",
                data=json.dumps(telemetry),
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
# Ingestion: Documents (contextualized chunking → standalone graph)
# ============================================================================


def create_document_chunker(chunk_size: int = 500) -> RecursiveChunker:
    """Create a Chonkie recursive chunker with paragraph -> sentence -> word hierarchy."""
    rules = RecursiveRules(
        [
            RecursiveLevel(delimiters=["\n\n"], include_delim="prev"),
            RecursiveLevel(delimiters=["\n"], include_delim="prev"),
            RecursiveLevel(delimiters=[".", "!", "?"], include_delim="prev"),
            RecursiveLevel(whitespace=True),
        ]
    )
    return RecursiveChunker(
        tokenizer="character",
        chunk_size=chunk_size,
        rules=rules,
        min_characters_per_chunk=24,
    )


def extract_document_title(filename: str, content: str) -> str:
    """Extract a human-readable document title from the content or filename.

    For markdown files, uses the first `# heading`. For plain text, uses the
    first non-empty line.  Falls back to a cleaned-up filename.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Markdown heading
        if stripped.startswith("# "):
            return stripped.lstrip("# ").strip()
        # First non-empty line for plain text
        return stripped
    # Fallback: derive from filename
    name = os.path.splitext(filename)[0]
    return name.replace("_", " ").replace("-", " ").title()


async def summarize_document(
    openai_client: AsyncOpenAI, full_document: str, title: str
) -> str:
    """Generate a one-sentence summary of the full document."""
    prompt = f"""<document>
{full_document}
</document>

Write a single sentence describing what this document is about. Start with the document title "{title}". Be concise — one sentence only."""

    response = await openai_client.chat.completions.create(
        model=LLM_CONTEXTUALIZATION_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=128,
    )

    return response.choices[0].message.content.strip()


async def contextualize_chunk(
    openai_client: AsyncOpenAI, full_document: str, chunk: str
) -> str:
    """Generate per-chunk contextualization: how the chunk fits in the document
    and resolution of any ambiguous pronouns."""
    prompt = f"""<document>
{full_document}
</document>

<chunk>
{chunk}
</chunk>

Write a brief contextualization for this chunk (1-2 sentences max). It should:
1. Explain where this chunk fits within the overall document (e.g. which section or topic it belongs to).
2. Resolve any ambiguous pronouns (he, she, it, they, them, this, these, those, etc.) — if the chunk uses a pronoun whose referent is not clear from the chunk alone, state what it refers to.

If there are no ambiguous pronouns, just provide the document context. Answer only with the contextualization and nothing else."""

    response = await openai_client.chat.completions.create(
        model=LLM_CONTEXTUALIZATION_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=192,
    )

    return response.choices[0].message.content.strip()


async def add_documents_to_zep(
    zep_client: AsyncZep,
    openai_client: AsyncOpenAI,
    documents: list[tuple[str, str]],
    graph_id: str = DOCUMENTS_GRAPH_ID,
) -> tuple[int, list[str]]:
    """
    Ingest documents into a standalone Zep graph using JSON episodes.

    Each chunk is sent as type="json" with:
      - source_description: document summary + per-chunk contextualization (non-extractable)
      - data: JSON with "document_title" (extractable entity) and "content" (chunk text)

    The source_description combines a constant document-level summary with a
    per-chunk contextualization that explains where the chunk fits in the
    document and resolves any ambiguous pronouns. This leverages Graphiti's
    JSON extraction path where source_description is provided as context to
    the LLM without being an extraction target itself.

    Returns tuple of (total chunks added, list of episode UUIDs).
    """
    if not documents:
        return 0, []

    # Create the standalone graph (ignore if it already exists)
    try:
        await zep_client.graph.create(
            graph_id=graph_id,
            name="Shared Documents",
            description="Shared reference documents for all users",
        )
        print(f"✓ Created standalone graph: {graph_id}")
    except Exception:
        print(f"  Standalone graph {graph_id} already exists")

    print(f"\nIngesting {len(documents)} document(s) into graph {graph_id}")
    total_added = 0
    episode_uuids = []

    for filename, content in documents:
        print(f"\n  Processing: {filename}")

        # Extract title and generate a one-sentence document summary
        title = extract_document_title(filename, content)
        doc_summary = await summarize_document(openai_client, content, title)
        print(f"  Title: {title}")
        print(f"  Document summary: {doc_summary}")

        # Small documents don't need chunking — document summary is sufficient
        if len(content) <= CHUNK_SIZE:
            data = json.dumps({"document_title": title, "content": content})
            try:
                episode = await zep_client.graph.add(
                    graph_id=graph_id,
                    type="json",
                    data=data,
                    source_description=doc_summary,
                )
                total_added += 1
                episode_uuids.append(episode.uuid_)
                print(f"  ✓ Added {filename} as single chunk")
            except Exception as e:
                print(f"  Error adding {filename}: {e}")
            continue

        # Chunk using Chonkie
        chunker = create_document_chunker(CHUNK_SIZE)
        raw_chunks = chunker.chunk(content)
        chunks = [(i, c.text) for i, c in enumerate(raw_chunks)]
        print(f"  Split into {len(chunks)} chunks")

        added_for_file = 0
        for chunk_index, chunk_text in chunks:
            try:
                # Per-chunk contextualization (pronoun resolution + section context)
                chunk_context = await contextualize_chunk(
                    openai_client, content, chunk_text
                )
                source_desc = f"{doc_summary} | Chunk context: {chunk_context}"

                data = json.dumps({"document_title": title, "content": chunk_text})

                # Zep has a 10k character limit per episode
                if len(data) > 10000:
                    data = data[:10000]

                episode = await zep_client.graph.add(
                    graph_id=graph_id,
                    type="json",
                    data=data,
                    source_description=source_desc,
                )
                total_added += 1
                added_for_file += 1
                episode_uuids.append(episode.uuid_)
            except Exception as e:
                print(f"  Error adding chunk {chunk_index} of {filename}: {e}")
                continue

        print(f"  ✓ Added {added_for_file}/{len(chunks)} chunks from {filename}")

    print(f"✓ Completed adding {total_added} document chunks to graph {graph_id}")
    return total_added, episode_uuids


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
        for uuid in remaining:
            try:
                episode = await zep_client.graph.episode.get(uuid)
                if episode.processed:
                    newly_processed.append(uuid)
            except Exception:
                pass  # Episode may not be available yet, retry next cycle

        for uuid in newly_processed:
            remaining.discard(uuid)
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
    if conversations:
        first = user_def.get("first_name", "")
        last = user_def.get("last_name", "")
        full_name = f"{first} {last}".strip() or None

        thread_ids = await add_conversations_to_zep(
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


def write_run_manifest(
    run_number,
    run_data,
    use_custom_ontology=False,
    use_custom_instructions=False,
    use_user_summary_instructions=False,
    num_doc_chunks=0,
    doc_graph_id=None,
):
    """
    Write a manifest file for the current run.
    Format: runs/{number}_{ISO8601_timestamp}/
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
        "custom_instructions": {
            "enabled": use_custom_instructions,
            "instruction_names": CUSTOM_INSTRUCTION_NAMES if use_custom_instructions else [],
        },
        "user_summary_instructions": {
            "enabled": use_user_summary_instructions,
            "instruction_names": USER_SUMMARY_INSTRUCTION_NAMES if use_user_summary_instructions else [],
        },
        "documents": {
            "graph_id": doc_graph_id,
            "num_chunks": num_doc_chunks,
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
        description="Zep Eval Harness — Ingestion Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_ingest.py                                   # Ingest all, poll until done
  uv run zep_ingest.py --no-poll                         # Ingest all, don't wait
  uv run zep_ingest.py --graphs zep_eval_test_user_001   # Ingest one user only
  uv run zep_ingest.py --graphs documents                # Ingest documents only
  uv run zep_ingest.py --graphs zep_eval_test_user_001,documents
  uv run zep_ingest.py --custom-ontology                 # Use custom ontology
  uv run zep_ingest.py --custom-instructions             # Use custom instructions
  uv run zep_ingest.py --user-summary-instructions       # Use user summary instructions
""",
    )
    parser.add_argument(
        "--custom-ontology",
        action="store_true",
        help="Use custom ontology instead of Zep defaults",
    )
    parser.add_argument(
        "--custom-instructions",
        action="store_true",
        help="Use custom instructions for graph extraction (domain context)",
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
            "Comma-separated list of graphs to ingest. "
            "Use user base IDs (e.g. zep_eval_test_user_001) and/or 'documents'. "
            "Default: all"
        ),
    )
    return parser.parse_args()


# ============================================================================
# Main
# ============================================================================


async def main():
    load_dotenv()
    args = parse_args()

    # Validate custom ontology flag
    if args.custom_ontology:
        if not CUSTOM_ONTOLOGY_AVAILABLE:
            print("Error: Custom ontology module could not be loaded")
            print("   Check that ontology.py exists and is valid")
            exit(1)

    # Validate custom instructions flag
    if args.custom_instructions:
        if not CUSTOM_INSTRUCTIONS_AVAILABLE:
            print("Error: Custom instructions module could not be loaded")
            print("   Check that custom_instructions.py exists and is valid")
            exit(1)

    # Validate user summary instructions flag
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

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        print("Error: Missing GOOGLE_API_KEY environment variable")
        print("   Required for document contextualization during ingestion")
        exit(1)

    # Initialize clients
    zep_client = AsyncZep(api_key=api_key)
    openai_client = AsyncOpenAI(api_key=google_api_key, base_url=GEMINI_BASE_URL)

    # Determine which graphs to ingest
    should_poll = not args.no_poll

    user_definitions = load_user_definitions()
    if not user_definitions:
        print("Error: No users found in data/users.json")
        exit(1)

    documents = load_documents()

    ingest_documents_flag = True
    selected_user_defs = user_definitions

    if args.graphs:
        selected = set(g.strip() for g in args.graphs.split(","))
        ingest_documents_flag = "documents" in selected
        selected_user_ids = selected - {"documents"}
        if selected_user_ids:
            selected_user_defs = [
                u for u in user_definitions if u["user_id"] in selected_user_ids
            ]
            unknown = selected_user_ids - {u["user_id"] for u in user_definitions}
            if unknown:
                print(f"Warning: Unknown graph IDs (not in users.json): {unknown}")
        else:
            selected_user_defs = []

    # Print header
    print("=" * 80)
    print("ZEP INGESTION SCRIPT")
    print("=" * 80)
    if args.custom_ontology:
        print("Ontology: Custom (default ontology suppressed)")
    else:
        print("Ontology: Default Zep ontology")
    print(f"Custom instructions: {'enabled' if args.custom_instructions else 'disabled'}")
    print(f"User summary instructions: {'enabled' if args.user_summary_instructions else 'disabled'}")
    print(f"Polling: {'enabled' if should_poll else 'disabled'}")
    print(f"Users: {len(selected_user_defs)}")
    print(f"Documents: {'yes' if ingest_documents_flag and documents else 'no'}")
    print("=" * 80)

    try:
        run_number = get_next_run_number()
        print(f"\nStarting run #{run_number}\n")

        # Launch all ingestion tasks in parallel
        user_tasks = [
            ingest_user(
                zep_client,
                user_def,
                args.custom_ontology,
                use_custom_instructions=args.custom_instructions,
                use_user_summary_instructions=args.user_summary_instructions,
            )
            for user_def in selected_user_defs
        ]

        doc_task = None
        doc_graph_id = None
        if ingest_documents_flag and documents:
            doc_graph_id = f"{DOCUMENTS_GRAPH_ID}_{uuid.uuid4().hex[:8]}"
            doc_task = add_documents_to_zep(
                zep_client, openai_client, documents, graph_id=doc_graph_id
            )

        # Gather all tasks concurrently (return_exceptions so one failure
        # doesn't cancel the rest)
        if doc_task:
            results = await asyncio.gather(
                *user_tasks, doc_task, return_exceptions=True
            )
            raw_user_results = results[:-1]
            doc_result = results[-1]
        elif user_tasks:
            raw_user_results = await asyncio.gather(
                *user_tasks, return_exceptions=True
            )
            doc_result = (0, [])
        else:
            raw_user_results = []
            doc_result = (0, [])

        # Separate successful results from failures
        run_data = []
        for i, result in enumerate(raw_user_results):
            if isinstance(result, Exception):
                base_id = selected_user_defs[i]["user_id"]
                print(f"⚠ Ingestion failed for {base_id}: {result}")
            else:
                run_data.append(result)

        if isinstance(doc_result, Exception):
            print(f"⚠ Document ingestion failed: {doc_result}")
            num_doc_chunks = 0
            doc_episode_uuids = []
        else:
            num_doc_chunks, doc_episode_uuids = doc_result

        # Write run manifest
        run_dir = write_run_manifest(
            run_number,
            run_data,
            use_custom_ontology=args.custom_ontology,
            use_custom_instructions=args.custom_instructions,
            use_user_summary_instructions=args.user_summary_instructions,
            num_doc_chunks=num_doc_chunks,
            doc_graph_id=doc_graph_id,
        )

        print("\n" + "=" * 80)
        print("INGESTION COMPLETE")
        print("=" * 80)
        print(f"\nRun #{run_number}")
        print(f"Manifest: {run_dir}/manifest.json")
        print(f"Users: {len(selected_user_defs)}")
        if num_doc_chunks:
            print(f"Document chunks: {num_doc_chunks}")

        # Poll for processing completion
        if should_poll:
            print("\n" + "=" * 80)
            print("POLLING FOR PROCESSING COMPLETION")
            print("=" * 80)
            print(
                f"Checking every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT}s)\n"
            )

            poll_tasks = []

            # Poll each user's telemetry episodes
            for user_data in run_data:
                uuids = user_data.get("episode_uuids", [])
                if uuids:
                    label = user_data["zep_user_id"]
                    poll_tasks.append(
                        poll_episode_uuids(zep_client, uuids, label)
                    )

            # Poll the document graph episodes
            if doc_episode_uuids:
                poll_tasks.append(
                    poll_episode_uuids(
                        zep_client, doc_episode_uuids, doc_graph_id
                    )
                )

            if poll_tasks:
                await asyncio.gather(*poll_tasks)
                print("\n✓ All graphs finished processing")
            else:
                print("No graphs to poll.")

            print(
                f"\nYou can now run: uv run zep_evaluate.py {run_number}"
            )
        else:
            print("\nGraph processing happens asynchronously and may take several minutes.")
            print(
                f"You can run zep_evaluate.py with run #{run_number} once processing is complete."
            )

    except Exception as e:
        print(f"\nScript failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
