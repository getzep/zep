import os
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
from retry import retry_with_backoff

# Import document ontology module
try:
    from ontology import (
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
    from custom_instructions import (
        set_document_custom_instructions,
        DOCUMENT_INSTRUCTION_NAMES,
    )

    DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE = True
except (ImportError, NotImplementedError):
    DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE = False
    DOCUMENT_INSTRUCTION_NAMES = []


CHECKPOINT_DIR = "runs/checkpoints"


# ============================================================================
# Checkpoint Management
# ============================================================================


def checkpoint_path_for_graph(graph_id: str) -> str:
    """Return the checkpoint file path for a given graph ID."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"doc_{graph_id}.json")


def save_checkpoint(path: str, data: dict):
    """Atomically write checkpoint data to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_checkpoint(path: str) -> dict:
    """Load checkpoint data from disk."""
    with open(path, "r") as f:
        return json.load(f)


def delete_checkpoint(path: str):
    """Remove a checkpoint file after successful completion."""
    if os.path.exists(path):
        os.remove(path)
        print(f"✓ Checkpoint removed: {path}")


# ============================================================================
# Data Loading
# ============================================================================


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
# Document Chunking & Contextualization
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

    Priority order:
    1. First markdown heading (any level: #, ##, ###, etc.)
    2. First short non-empty line (≤120 chars, likely a title rather than a paragraph)
    3. Cleaned-up filename as fallback
    """
    first_short_line = None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Markdown heading at any level
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        # Remember the first short line as a fallback title candidate
        if first_short_line is None and len(stripped) <= 120:
            first_short_line = stripped

    if first_short_line:
        return first_short_line

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

Write a single sentence describing what this document is about. Be concise — one sentence only."""

    response = await retry_with_backoff(
        openai_client.chat.completions.create,
        model=LLM_CONTEXTUALIZATION_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=128,
        description=f"summarize '{title}'",
    )

    return response.choices[0].message.content.strip()


async def contextualize_chunk(
    openai_client: AsyncOpenAI, full_document: str, chunk: str, chunk_label: str = "chunk"
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

    response = await retry_with_backoff(
        openai_client.chat.completions.create,
        model=LLM_CONTEXTUALIZATION_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=192,
        description=f"contextualize {chunk_label}",
    )

    return response.choices[0].message.content.strip()


# ============================================================================
# Ingestion: Documents (contextualized chunking → standalone graph)
# ============================================================================


async def add_documents_to_zep(
    zep_client: AsyncZep,
    openai_client: AsyncOpenAI,
    documents: list[tuple[str, str]],
    graph_id: str = DOCUMENTS_GRAPH_ID,
    use_document_custom_ontology: bool = False,
    use_document_custom_instructions: bool = False,
    checkpoint_data: dict | None = None,
    run_number: int = 0,
) -> tuple[int, list[str]]:
    """
    Ingest documents into a standalone Zep graph using JSON episodes.

    Each chunk is sent as type="json" with a data payload containing:
      - "document_title", "document_summary", and "content" (for small documents)
      - "document_title", "document_summary", "chunk_context", and "content" (for chunked documents)

    Supports checkpoint/resume: if checkpoint_data is provided, skips already-ingested
    documents and chunks. Saves checkpoint after each chunk so ingestion can be resumed
    if interrupted.

    Args:
        zep_client: AsyncZep client instance
        openai_client: AsyncOpenAI client for contextualization
        documents: List of (filename, content) tuples
        graph_id: Standalone graph ID
        use_document_custom_ontology: Apply document-specific custom ontology
        use_document_custom_instructions: Apply document-specific custom instructions
        checkpoint_data: Existing checkpoint to resume from (None for fresh start)

    Returns tuple of (total chunks added, list of episode UUIDs).
    """
    if not documents:
        return 0, []

    cp_path = checkpoint_path_for_graph(graph_id)
    is_resuming = checkpoint_data is not None

    # Initialize or restore checkpoint state
    if is_resuming:
        # Validate chunk_size matches — different chunk sizes produce different chunks
        saved_chunk_size = checkpoint_data.get("chunk_size")
        if saved_chunk_size is not None and saved_chunk_size != CHUNK_SIZE:
            print(f"  ✗ CHUNK_SIZE mismatch: checkpoint was created with {saved_chunk_size}, "
                  f"but current CHUNK_SIZE is {CHUNK_SIZE}")
            print(f"    Resuming with a different chunk size would corrupt data.")
            print(f"    Either restore CHUNK_SIZE to {saved_chunk_size} or start a fresh ingestion.")
            raise ValueError(f"CHUNK_SIZE mismatch: checkpoint={saved_chunk_size}, current={CHUNK_SIZE}")

        completed_docs = set(checkpoint_data.get("completed_documents", []))
        episode_uuids = list(checkpoint_data.get("episode_uuids", []))
        total_added = checkpoint_data.get("total_chunks_submitted", 0)
        in_progress = checkpoint_data.get("current_document")
        print(f"\n  Resuming: {len(completed_docs)} documents already done, {total_added} chunks submitted")
    else:
        completed_docs = set()
        episode_uuids = []
        total_added = 0
        in_progress = None

    if not is_resuming:
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

        # Apply document-specific ontology BEFORE ingesting data
        if use_document_custom_ontology:
            print(f"\nSetting document custom ontology for graph: {graph_id}")
            try:
                await set_document_custom_ontology(zep_client, graph_ids=[graph_id])
                print("✓ Document custom ontology applied successfully")
            except Exception as e:
                print(f"Error setting document ontology: {e}")
                raise

        # Apply document-specific custom instructions BEFORE ingesting data
        if use_document_custom_instructions:
            print(f"Setting document custom instructions for graph: {graph_id}")
            try:
                await set_document_custom_instructions(zep_client, graph_ids=[graph_id])
                print("✓ Document custom instructions applied successfully")
            except Exception as e:
                print(f"Error setting document custom instructions: {e}")
                raise

    print(f"\nIngesting {len(documents)} document(s) into graph {graph_id}")

    for filename, content in documents:
        # Skip completed documents
        if filename in completed_docs:
            print(f"\n  Skipping (already done): {filename}")
            continue

        print(f"\n  Processing: {filename}")

        # Determine how many chunks to skip for in-progress document
        skip_chunks = 0
        cached_title = None
        cached_summary = None
        if is_resuming and in_progress and in_progress.get("filename") == filename:
            skip_chunks = in_progress.get("chunks_submitted", 0)
            cached_title = in_progress.get("title")
            cached_summary = in_progress.get("doc_summary")
            print(f"  Resuming from chunk {skip_chunks}")

        # Extract title and generate a one-sentence document summary
        title = cached_title or extract_document_title(filename, content)
        doc_summary = cached_summary or await summarize_document(openai_client, content, title)
        print(f"  Title: {title}")
        print(f"  Document summary: {doc_summary}")

        # Small documents don't need chunking — document summary is sufficient
        if len(content) <= CHUNK_SIZE:
            if skip_chunks > 0:
                # Already submitted this single-chunk document
                completed_docs.add(filename)
                continue

            data = json.dumps({"document_title": title, "document_summary": doc_summary, "content": content})
            try:
                episode = await retry_with_backoff(
                    zep_client.graph.add,
                    graph_id=graph_id,
                    type="json",
                    data=data,
                    description=f"add '{filename}' (single chunk)",
                )
                total_added += 1
                episode_uuids.append(episode.uuid_)
                print(f"  ✓ Added {filename} as single chunk")
            except Exception as e:
                print(f"  ✗ Failed to add {filename} after retries: {e}")
                # Save checkpoint and abort
                save_checkpoint(cp_path, {
                    "graph_id": graph_id,
                    "run_number": run_number,
                    "chunk_size": CHUNK_SIZE,
                    "config": {
                        "custom_ontology": use_document_custom_ontology,
                        "custom_instructions": use_document_custom_instructions,
                    },
                    "completed_documents": sorted(completed_docs),
                    "current_document": {
                        "filename": filename,
                        "title": title,
                        "doc_summary": doc_summary,
                        "total_chunks": 1,
                        "chunks_submitted": 0,
                    },
                    "episode_uuids": episode_uuids,
                    "total_chunks_submitted": total_added,
                })
                print(f"\n  ✗ Ingestion halted. Checkpoint saved to: {cp_path}")
                print(f"    Resume with: uv run zep_ingest_documents.py --resume {cp_path}")
                raise

            completed_docs.add(filename)
            # Save checkpoint after completing this document
            save_checkpoint(cp_path, {
                "graph_id": graph_id,
                "run_number": run_number,
                "chunk_size": CHUNK_SIZE,
                "config": {
                    "custom_ontology": use_document_custom_ontology,
                    "custom_instructions": use_document_custom_instructions,
                },
                "completed_documents": sorted(completed_docs),
                "current_document": None,
                "episode_uuids": episode_uuids,
                "total_chunks_submitted": total_added,
            })
            continue

        # Chunk using Chonkie
        chunker = create_document_chunker(CHUNK_SIZE)
        raw_chunks = chunker.chunk(content)
        chunks = [(i, c.text) for i, c in enumerate(raw_chunks)]
        print(f"  Split into {len(chunks)} chunks (skipping first {skip_chunks})")

        added_for_file = 0
        for chunk_index, chunk_text in chunks:
            # Skip chunks already submitted in a previous run
            if chunk_index < skip_chunks:
                added_for_file += 1
                continue

            chunk_label = f"chunk {chunk_index + 1}/{len(chunks)} of '{filename}'"

            try:
                # Per-chunk contextualization (pronoun resolution + section context)
                chunk_context = await contextualize_chunk(
                    openai_client, content, chunk_text, chunk_label=chunk_label
                )

                data = json.dumps({"document_title": title, "document_summary": doc_summary, "chunk_context": chunk_context, "content": chunk_text})

                episode = await retry_with_backoff(
                    zep_client.graph.add,
                    graph_id=graph_id,
                    type="json",
                    data=data,
                    description=f"add {chunk_label}",
                )
                total_added += 1
                added_for_file += 1
                episode_uuids.append(episode.uuid_)

                # Save checkpoint after each chunk
                save_checkpoint(cp_path, {
                    "graph_id": graph_id,
                    "run_number": run_number,
                    "chunk_size": CHUNK_SIZE,
                    "config": {
                        "custom_ontology": use_document_custom_ontology,
                        "custom_instructions": use_document_custom_instructions,
                    },
                    "completed_documents": sorted(completed_docs),
                    "current_document": {
                        "filename": filename,
                        "title": title,
                        "doc_summary": doc_summary,
                        "total_chunks": len(chunks),
                        "chunks_submitted": chunk_index + 1,
                    },
                    "episode_uuids": episode_uuids,
                    "total_chunks_submitted": total_added,
                })
            except Exception as e:
                print(f"  ✗ Failed {chunk_label} after retries: {e}")
                # Save checkpoint and abort
                save_checkpoint(cp_path, {
                    "graph_id": graph_id,
                    "run_number": run_number,
                    "chunk_size": CHUNK_SIZE,
                    "config": {
                        "custom_ontology": use_document_custom_ontology,
                        "custom_instructions": use_document_custom_instructions,
                    },
                    "completed_documents": sorted(completed_docs),
                    "current_document": {
                        "filename": filename,
                        "title": title,
                        "doc_summary": doc_summary,
                        "total_chunks": len(chunks),
                        "chunks_submitted": chunk_index,
                    },
                    "episode_uuids": episode_uuids,
                    "total_chunks_submitted": total_added,
                })
                print(f"\n  ✗ Ingestion halted. Checkpoint saved to: {cp_path}")
                print(f"    Resume with: uv run zep_ingest_documents.py --resume {cp_path}")
                raise

        completed_docs.add(filename)
        print(f"  ✓ Added {added_for_file}/{len(chunks)} chunks from {filename}")

    print(f"✓ Completed adding {total_added} document chunks to graph {graph_id}")

    # Clean up checkpoint on successful completion
    delete_checkpoint(cp_path)

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
# Run Manifest
# ============================================================================


def get_next_run_number():
    """
    Get the next run number by checking existing run directories
    in runs/documents/.
    """
    os.makedirs("runs/documents", exist_ok=True)
    existing_runs = glob.glob("runs/documents/*")

    if not existing_runs:
        return 1

    # Extract run numbers from directory names (format: runs/documents/1_timestamp)
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
    graph_id,
    num_chunks,
    use_document_custom_ontology=False,
    use_document_custom_instructions=False,
):
    """
    Write a manifest file for the current document ingestion run.
    Format: runs/documents/{number}_{ISO8601_timestamp}/
    """
    timestamp = datetime.now().isoformat()
    # Use ISO 8601 format with basic format (no colons for filesystem compatibility)
    timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = f"runs/documents/{run_number}_{timestamp_str}"
    os.makedirs(run_dir, exist_ok=True)

    manifest = {
        "run_number": run_number,
        "type": "documents",
        "timestamp": timestamp,
        "graph_id": graph_id,
        "num_chunks": num_chunks,
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
  uv run zep_ingest_documents.py                          # Ingest documents, poll until done
  uv run zep_ingest_documents.py --no-poll                # Ingest documents, don't wait
  uv run zep_ingest_documents.py --custom-ontology        # Use custom document ontology
  uv run zep_ingest_documents.py --custom-instructions    # Use custom document instructions
  uv run zep_ingest_documents.py --resume runs/checkpoints/doc_xxx.json  # Resume from checkpoint
""",
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
        help="Don't wait for processing",
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
        print(f"  Graph ID: {checkpoint_data['graph_id']}")
        print(f"  Completed documents: {len(checkpoint_data.get('completed_documents', []))}")
        print(f"  Chunks submitted: {checkpoint_data.get('total_chunks_submitted', 0)}")

        # Restore config from checkpoint
        config = checkpoint_data.get("config", {})
        args.custom_ontology = config.get("custom_ontology", False)
        args.custom_instructions = config.get("custom_instructions", False)

    # Validate document graph flags
    if args.custom_ontology:
        if not DOCUMENT_CUSTOM_ONTOLOGY_AVAILABLE:
            print("Error: Document custom ontology module could not be loaded")
            print("   Check that ontology.py exists and contains document ontology definitions")
            exit(1)

    if args.custom_instructions:
        if not DOCUMENT_CUSTOM_INSTRUCTIONS_AVAILABLE:
            print("Error: Document custom instructions module could not be loaded")
            print("   Check that custom_instructions.py exists and contains document instruction definitions")
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

    # Load documents
    documents = load_documents()
    if not documents:
        print("Error: No documents found in data/documents/")
        exit(1)

    should_poll = not args.no_poll

    # Print header
    print("=" * 80)
    print("ZEP DOCUMENT GRAPH INGESTION" + (" (RESUMING)" if checkpoint_data else ""))
    print("=" * 80)
    print("--- Document Graph ---")
    if args.custom_ontology:
        print("  Ontology: Custom document ontology")
    else:
        print("  Ontology: Default Zep ontology")
    print(f"  Custom instructions: {'enabled' if args.custom_instructions else 'disabled'}")
    print("---")
    print(f"Polling: {'enabled' if should_poll else 'disabled'}")
    print(f"Documents: {len(documents)}")
    print("=" * 80)

    try:
        # Use checkpoint graph_id or generate a new one
        if checkpoint_data:
            graph_id = checkpoint_data["graph_id"]
            # Recover run_number from checkpoint if available
            run_number = checkpoint_data.get("run_number", get_next_run_number())
        else:
            run_number = get_next_run_number()
            graph_id = f"{DOCUMENTS_GRAPH_ID}_{uuid.uuid4().hex[:8]}"

        print(f"\n{'Resuming' if checkpoint_data else 'Starting'} document run #{run_number}\n")

        # Ingest documents
        num_doc_chunks, doc_episode_uuids = await add_documents_to_zep(
            zep_client,
            openai_client,
            documents,
            graph_id=graph_id,
            use_document_custom_ontology=args.custom_ontology,
            use_document_custom_instructions=args.custom_instructions,
            checkpoint_data=checkpoint_data,
            run_number=run_number,
        )

        # Write run manifest
        run_dir = write_run_manifest(
            run_number,
            graph_id,
            num_doc_chunks,
            use_document_custom_ontology=args.custom_ontology,
            use_document_custom_instructions=args.custom_instructions,
        )

        print("\n" + "=" * 80)
        print("DOCUMENT INGESTION COMPLETE")
        print("=" * 80)
        print(f"\nRun #{run_number}")
        print(f"Manifest: {run_dir}/manifest.json")
        print(f"Document chunks: {num_doc_chunks}")

        # Poll for processing completion
        if should_poll:
            print("\n" + "=" * 80)
            print("POLLING FOR PROCESSING COMPLETION")
            print("=" * 80)
            print(
                f"Checking every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT}s)\n"
            )

            if doc_episode_uuids:
                await poll_episode_uuids(
                    zep_client, doc_episode_uuids, graph_id
                )
                print("\n✓ Document graph finished processing")
            else:
                print("No episodes to poll.")

            print(
                f"\nYou can now run: uv run zep_evaluate.py --doc-run {run_number}"
            )
        else:
            print("\nGraph processing happens asynchronously and may take several minutes.")
            print(
                f"You can run zep_evaluate.py with --doc-run {run_number} once processing is complete."
            )

    except Exception as e:
        print(f"\nScript failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
