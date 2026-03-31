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

    Args:
        zep_client: AsyncZep client instance
        openai_client: AsyncOpenAI client for contextualization
        documents: List of (filename, content) tuples
        graph_id: Standalone graph ID
        use_document_custom_ontology: Apply document-specific custom ontology
        use_document_custom_instructions: Apply document-specific custom instructions

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
    return parser.parse_args()


# ============================================================================
# Main
# ============================================================================


async def main():
    load_dotenv()
    args = parse_args()

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
    print("ZEP DOCUMENT GRAPH INGESTION")
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
        run_number = get_next_run_number()
        print(f"\nStarting document run #{run_number}\n")

        # Generate unique graph ID
        graph_id = f"{DOCUMENTS_GRAPH_ID}_{uuid.uuid4().hex[:8]}"

        # Ingest documents
        num_doc_chunks, doc_episode_uuids = await add_documents_to_zep(
            zep_client,
            openai_client,
            documents,
            graph_id=graph_id,
            use_document_custom_ontology=args.custom_ontology,
            use_document_custom_instructions=args.custom_instructions,
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
