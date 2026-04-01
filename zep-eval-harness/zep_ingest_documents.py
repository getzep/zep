"""
Zep Eval Harness — Document Graph Ingestion Script

Ingests pre-prepared document chunks into a standalone Zep graph. Reads chunks
from a chunk set directory (produced by zep_chunk_documents.py) and sends them
to Zep via graph.add().

Supports "follow" mode: if the targeted chunk set is still being generated
(status: "in_progress"), the script tails the JSONL file and ingests chunks
as they become available.

Also supports inline mode (--chunk-size) for convenience: runs chunking first,
writes the chunk set, then ingests from it.
"""

import os
import json
import glob
import uuid
import shutil
import asyncio
import argparse
from time import time
from datetime import datetime
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep

from config.constants import POLL_INTERVAL, POLL_TIMEOUT
from config.document_ingestion.constants import DOCUMENTS_GRAPH_ID
from config.document_chunking.constants import CHUNK_SIZE
from retry import retry_with_backoff
from checkpoint import save_checkpoint, load_checkpoint, delete_checkpoint

# Import document ontology module
try:
    from config.document_ingestion.ontology import (
        set_document_custom_ontology,
        DOCUMENT_ENTITY_TYPES,
        DOCUMENT_EDGE_TYPES,
    )

    DOCUMENT_CUSTOM_ONTOLOGY_AVAILABLE = True
except (ImportError, NotImplementedError):
    DOCUMENT_CUSTOM_ONTOLOGY_AVAILABLE = False
    DOCUMENT_ENTITY_TYPES = []
    DOCUMENT_EDGE_TYPES = []

# Import document custom instructions module
try:
    from config.document_ingestion.custom_instructions import (
        set_document_custom_instructions,
        DOCUMENT_INSTRUCTION_NAMES,
    )

    DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE = False
    DOCUMENT_INSTRUCTION_NAMES = []


CHECKPOINT_DIR = "runs/checkpoints"
CHUNK_SETS_DIR = "runs/chunk_sets"
FOLLOW_POLL_INTERVAL = 3  # seconds between checks when tailing in-progress chunk set


# ============================================================================
# Checkpoint Management
# ============================================================================


def checkpoint_path_for_graph(graph_id: str) -> str:
    """Return the checkpoint file path for a given graph ID."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"doc_{graph_id}.json")


# ============================================================================
# Chunk Set Reading
# ============================================================================


def find_chunk_set_dir(chunk_set_number: int) -> str:
    """Find the chunk set directory for a given run number."""
    matches = glob.glob(os.path.join(CHUNK_SETS_DIR, f"{chunk_set_number}_*"))
    dirs = [m for m in matches if os.path.isdir(m)]
    if not dirs:
        raise FileNotFoundError(
            f"No chunk set found for number {chunk_set_number} in {CHUNK_SETS_DIR}"
        )
    if len(dirs) > 1:
        # Pick the most recent by timestamp in directory name
        dirs.sort()
        return dirs[-1]
    return dirs[0]


def read_chunk_set_meta(chunk_set_dir: str) -> dict:
    """Read the meta.json from a chunk set directory."""
    meta_path = os.path.join(chunk_set_dir, "meta.json")
    with open(meta_path, "r") as f:
        return json.load(f)



FOLLOW_TIMEOUT = 3600  # 1 hour max wait for an in-progress chunk set


async def follow_and_ingest(
    zep_client: AsyncZep,
    chunk_set_dir: str,
    graph_id: str,
    run_number: int = 0,
    ingested_count: int = 0,
    prior_episode_uuids: list[str] | None = None,
    config: dict | None = None,
) -> tuple[int, list[str]]:
    """
    Tail a chunk set's JSONL file and ingest chunks as they appear.
    Handles both complete and in-progress chunk sets.

    Args:
        zep_client: AsyncZep client instance
        chunk_set_dir: Path to chunk set directory
        graph_id: Zep graph ID to ingest into
        run_number: Run number for checkpoint persistence
        ingested_count: Number of chunks already ingested (for resume)
        prior_episode_uuids: Episode UUIDs from a previous run (for resume)
        config: Ontology/instruction config flags to persist in checkpoint

    Returns tuple of (total chunks ingested, all episode UUIDs including prior).
    """
    jsonl_path = os.path.join(chunk_set_dir, "chunks.jsonl")

    cp_path = checkpoint_path_for_graph(graph_id)
    total_ingested = ingested_count
    episode_uuids = list(prior_episode_uuids or [])
    last_line_read = ingested_count
    file_offset = 0  # Track read position to avoid re-reading entire file
    follow_start = time()

    while True:
        # Read new lines from where we left off
        new_chunks = []
        if os.path.exists(jsonl_path):
            with open(jsonl_path, "r") as f:
                f.seek(file_offset)
                for line in f:
                    if not line.endswith("\n"):
                        break  # Partial line — stop here
                    line_stripped = line.strip()
                    if not line_stripped:
                        file_offset += len(line.encode())
                        continue
                    try:
                        new_chunks.append(json.loads(line_stripped))
                        file_offset += len(line.encode())
                    except json.JSONDecodeError:
                        file_offset += len(line.encode())
                        continue

        for chunk in new_chunks:
            chunk_label = f"chunk {chunk['chunk_index'] + 1}/{chunk['total_chunks']} of '{chunk['filename']}'"

            # Build JSON data payload
            data_dict = {
                "document_title": chunk["title"],
                "document_summary": chunk["summary"],
                "content": chunk["content"],
            }
            if chunk.get("context"):
                data_dict["chunk_context"] = chunk["context"]

            data = json.dumps(data_dict)

            try:
                episode = await retry_with_backoff(
                    zep_client.graph.add,
                    graph_id=graph_id,
                    type="json",
                    data=data,
                    description=f"ingest {chunk_label}",
                )
                total_ingested += 1
                episode_uuids.append(episode.uuid_)
                last_line_read += 1

                # Checkpoint after each chunk
                save_checkpoint(cp_path, {
                    "graph_id": graph_id,
                    "run_number": run_number,
                    "chunk_set_dir": chunk_set_dir,
                    "chunks_ingested": total_ingested,
                    "episode_uuids": episode_uuids,
                    "config": config or {},
                })
            except Exception as e:
                print(f"  ✗ Failed {chunk_label} after retries: {e}")
                save_checkpoint(cp_path, {
                    "graph_id": graph_id,
                    "run_number": run_number,
                    "chunk_set_dir": chunk_set_dir,
                    "chunks_ingested": total_ingested,
                    "episode_uuids": episode_uuids,
                    "config": config or {},
                })
                print(f"\n  ✗ Ingestion halted. Checkpoint saved to: {cp_path}")
                print(f"    Resume with: uv run zep_ingest_documents.py --resume {cp_path}")
                raise

        # Check if chunk set is complete (with graceful handling for missing/partial meta)
        try:
            meta = read_chunk_set_meta(chunk_set_dir)
            if meta["status"] == "complete" and last_line_read >= meta["num_chunks"]:
                break
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass  # Chunk set not ready yet — continue waiting

        if not new_chunks:
            # No new chunks available — check timeout
            elapsed = time() - follow_start
            if elapsed > FOLLOW_TIMEOUT:
                print(f"  ⚠ Follow mode timed out after {FOLLOW_TIMEOUT}s")
                break
            print(f"  Waiting for chunks... ({total_ingested} ingested, chunk set in progress)")
            await asyncio.sleep(FOLLOW_POLL_INTERVAL)
        else:
            # Reset timeout when we're making progress
            follow_start = time()

    # Clean up checkpoint on success
    delete_checkpoint(cp_path)

    return total_ingested, episode_uuids


# ============================================================================
# Graph Setup
# ============================================================================


async def setup_graph(
    zep_client: AsyncZep,
    graph_id: str,
    use_custom_ontology: bool = False,
    use_custom_instructions: bool = False,
):
    """Create the standalone graph and apply ontology/instructions."""
    try:
        await zep_client.graph.create(
            graph_id=graph_id,
            name="Shared Documents",
            description="Shared reference documents for all users",
        )
        print(f"✓ Created standalone graph: {graph_id}")
    except Exception:
        print(f"  Standalone graph {graph_id} already exists")

    if use_custom_ontology:
        print(f"\nSetting document custom ontology for graph: {graph_id}")
        try:
            await set_document_custom_ontology(zep_client, graph_ids=[graph_id])
            print("✓ Document custom ontology applied successfully")
        except Exception as e:
            print(f"Error setting document ontology: {e}")
            raise

    if use_custom_instructions:
        print(f"Setting document custom instructions for graph: {graph_id}")
        try:
            await set_document_custom_instructions(zep_client, graph_ids=[graph_id])
            print("✓ Document custom instructions applied successfully")
        except Exception as e:
            print(f"Error setting document custom instructions: {e}")
            raise


# ============================================================================
# Polling
# ============================================================================


async def poll_episode_uuids(
    zep_client: AsyncZep, episode_uuids: list[str], label: str
) -> int:
    """
    Poll individual episode UUIDs until all are processed.
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
                pass

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


# ============================================================================
# Run Manifest
# ============================================================================


def get_next_run_number():
    """Get the next run number for runs/documents/."""
    os.makedirs("runs/documents", exist_ok=True)
    existing_runs = glob.glob("runs/documents/*")

    run_numbers = []
    for run_dir in existing_runs:
        if not os.path.isdir(run_dir):
            continue
        try:
            run_num = int(os.path.basename(run_dir).split("_")[0])
            run_numbers.append(run_num)
        except (IndexError, ValueError):
            continue

    return max(run_numbers) + 1 if run_numbers else 1


def write_run_manifest(
    run_number,
    graph_id,
    num_chunks,
    chunk_set_number=None,
    chunk_size=None,
    use_document_custom_ontology=False,
    use_document_custom_instructions=False,
):
    """Write a manifest file for the current document ingestion run."""
    timestamp = datetime.now().isoformat()
    timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = f"runs/documents/{run_number}_{timestamp_str}"
    os.makedirs(run_dir, exist_ok=True)

    # Snapshot the document ingestion config used for this run
    snapshot_dir = os.path.join(run_dir, "config_snapshot")
    shutil.copytree(
        "config/document_ingestion", snapshot_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    manifest = {
        "run_number": run_number,
        "type": "documents",
        "timestamp": timestamp,
        "graph_id": graph_id,
        "num_chunks": num_chunks,
        "chunk_set": chunk_set_number,
        "chunk_size": chunk_size,
        "ontology": {
            "type": (
                "custom" if use_document_custom_ontology else "default_zep"
            ),
            "custom_entity_types": DOCUMENT_ENTITY_TYPES if use_document_custom_ontology else [],
            "custom_edge_types": DOCUMENT_EDGE_TYPES if use_document_custom_ontology else [],
        },
        "custom_instructions": {
            "enabled": use_document_custom_instructions,
            "instruction_names": DOCUMENT_INSTRUCTION_NAMES if use_document_custom_instructions else [],
        },
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
        description="Zep Eval Harness — Document Graph Ingestion Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_ingest_documents.py --chunk-set 1                        # Ingest chunk set #1
  uv run zep_ingest_documents.py --chunk-set 1 --custom-ontology      # With custom ontology
  uv run zep_ingest_documents.py --chunk-size 500                     # Inline: chunk then ingest
  uv run zep_ingest_documents.py --chunk-size 1000 --custom-ontology  # Inline with custom ontology
  uv run zep_ingest_documents.py --resume runs/checkpoints/doc_xxx.json
""",
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--chunk-set",
        type=int,
        default=None,
        help="Chunk set run number to ingest from (e.g., 1)",
    )
    source.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Inline mode: chunk at this size, then ingest (runs chunking first)",
    )
    source.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Path to a checkpoint file to resume a previously interrupted ingestion",
    )

    parser.add_argument(
        "--custom-ontology",
        action="store_true",
        help="Use custom ontology for document graph",
    )
    parser.add_argument(
        "--custom-instructions",
        action="store_true",
        help="Use custom instructions for document graph extraction",
    )
    parser.add_argument(
        "--no-poll",
        action="store_true",
        help="Don't wait for Zep processing to complete",
    )
    return parser.parse_args()


# ============================================================================
# Main
# ============================================================================


async def main():
    load_dotenv()
    args = parse_args()

    # Resolve chunk set source
    chunk_set_dir = None
    chunk_set_number = None
    checkpoint_data = None
    is_resuming = False

    if args.resume:
        # Resume from checkpoint
        if not os.path.exists(args.resume):
            print(f"Error: Checkpoint file not found: {args.resume}")
            exit(1)
        checkpoint_data = load_checkpoint(args.resume)
        chunk_set_dir = checkpoint_data["chunk_set_dir"]
        is_resuming = True
        print(f"✓ Loaded checkpoint from: {args.resume}")
        print(f"  Graph ID: {checkpoint_data['graph_id']}")
        print(f"  Chunks ingested: {checkpoint_data.get('chunks_ingested', 0)}")

        # Restore config from checkpoint
        config = checkpoint_data.get("config", {})
        args.custom_ontology = config.get("custom_ontology", args.custom_ontology)
        args.custom_instructions = config.get("custom_instructions", args.custom_instructions)

    elif args.chunk_size is not None:
        # Inline mode: run chunking first
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            print("Error: Missing GOOGLE_API_KEY (required for inline chunking)")
            exit(1)

        from openai import AsyncOpenAI
        from config.constants import GEMINI_BASE_URL
        from zep_chunk_documents import load_documents, run_chunking

        openai_client = AsyncOpenAI(api_key=google_api_key, base_url=GEMINI_BASE_URL)
        documents = load_documents()
        if not documents:
            print("Error: No documents found in data/documents/")
            exit(1)

        print("=" * 80)
        print("INLINE CHUNKING")
        print("=" * 80)
        print(f"  Chunk size: {args.chunk_size}")
        print(f"  Documents: {len(documents)}")
        print("=" * 80)

        chunk_set_dir = await run_chunking(
            openai_client, documents, args.chunk_size,
        )
        chunk_set_number = int(os.path.basename(chunk_set_dir).split("_")[0])
        print()  # Blank line before ingestion output

    elif args.chunk_set is not None:
        # Use pre-prepared chunk set
        chunk_set_number = args.chunk_set
        try:
            chunk_set_dir = find_chunk_set_dir(chunk_set_number)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            exit(1)

    # Validate chunk set
    meta = read_chunk_set_meta(chunk_set_dir)
    chunk_size = meta.get("chunk_size")
    if chunk_set_number is None:
        chunk_set_number = meta.get("chunk_set_number")

    print(f"✓ Using chunk set: {chunk_set_dir}")
    print(f"  Status: {meta['status']}")
    print(f"  Chunk size: {chunk_size}")
    print(f"  Chunks available: {meta.get('num_chunks', '?')}")

    # Validate Zep config
    if args.custom_ontology and not DOCUMENT_CUSTOM_ONTOLOGY_AVAILABLE:
        print("Error: Document custom ontology module could not be loaded")
        exit(1)
    if args.custom_instructions and not DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE:
        print("Error: Document custom instructions module could not be loaded")
        exit(1)

    api_key = os.getenv("ZEP_API_KEY")
    if not api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        exit(1)

    zep_client = AsyncZep(api_key=api_key)
    should_poll = not args.no_poll

    # Print header
    print("\n" + "=" * 80)
    print("ZEP DOCUMENT GRAPH INGESTION" + (" (RESUMING)" if is_resuming else ""))
    print("=" * 80)
    if args.custom_ontology:
        print("  Ontology: Custom document ontology")
    else:
        print("  Ontology: Default Zep ontology")
    print(f"  Custom instructions: {'enabled' if args.custom_instructions else 'disabled'}")
    print(f"  Polling: {'enabled' if should_poll else 'disabled'}")
    print("=" * 80)

    try:
        ingestion_config = {
            "custom_ontology": args.custom_ontology,
            "custom_instructions": args.custom_instructions,
        }

        if is_resuming:
            graph_id = checkpoint_data["graph_id"]
            run_number = checkpoint_data.get("run_number", get_next_run_number())
            ingested_count = checkpoint_data.get("chunks_ingested", 0)
            prior_episode_uuids = checkpoint_data.get("episode_uuids", [])
        else:
            run_number = get_next_run_number()
            graph_id = f"{DOCUMENTS_GRAPH_ID}_{uuid.uuid4().hex[:8]}"
            ingested_count = 0
            prior_episode_uuids = []

            # Create graph and apply ontology/instructions
            await setup_graph(
                zep_client, graph_id,
                use_custom_ontology=args.custom_ontology,
                use_custom_instructions=args.custom_instructions,
            )

        print(f"\n{'Resuming' if is_resuming else 'Starting'} document run #{run_number}")
        print(f"Graph ID: {graph_id}\n")

        # Ingest chunks (with follow mode for in-progress chunk sets)
        num_ingested, episode_uuids = await follow_and_ingest(
            zep_client, chunk_set_dir, graph_id,
            run_number=run_number,
            ingested_count=ingested_count,
            prior_episode_uuids=prior_episode_uuids,
            config=ingestion_config,
        )

        # Write run manifest
        run_dir = write_run_manifest(
            run_number, graph_id, num_ingested,
            chunk_set_number=chunk_set_number,
            chunk_size=chunk_size,
            use_document_custom_ontology=args.custom_ontology,
            use_document_custom_instructions=args.custom_instructions,
        )

        print("\n" + "=" * 80)
        print("DOCUMENT INGESTION COMPLETE")
        print("=" * 80)
        print(f"\nRun #{run_number}")
        print(f"Manifest: {run_dir}/manifest.json")
        print(f"Chunks ingested: {num_ingested}")

        # Poll for processing completion
        if should_poll:
            print("\n" + "=" * 80)
            print("POLLING FOR PROCESSING COMPLETION")
            print("=" * 80)
            print(f"Checking every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT}s)\n")

            if episode_uuids:
                await poll_episode_uuids(zep_client, episode_uuids, graph_id)
                print("\n✓ Document graph finished processing")
            else:
                print("No episodes to poll.")

            print(f"\nYou can now run: uv run zep_evaluate.py --doc-run {run_number}")
        else:
            print("\nGraph processing happens asynchronously and may take several minutes.")
            print(f"You can run zep_evaluate.py with --doc-run {run_number} once processing is complete.")

    except Exception as e:
        print(f"\nScript failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
