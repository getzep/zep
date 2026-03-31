"""
Zep Eval Harness — Document Chunking & Contextualization Script

Chunks documents and generates LLM-based summaries and per-chunk contextualizations.
Writes results to a chunk set directory (runs/chunk_sets/{N}_{timestamp}/) as JSONL,
appending one line per chunk as it completes. This allows the ingestion script to
tail the JSONL and ingest chunks as they become available.

The chunk set can be reused across multiple ingestion runs with different ontology
or custom instruction configurations, avoiding redundant LLM calls.
"""

import os
import json
import glob
import asyncio
import argparse
from time import time
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from chonkie import RecursiveChunker, RecursiveRules, RecursiveLevel

from constants import (
    CHUNK_SIZE,
    DOCUMENT_INGEST_LIMIT,
    GEMINI_BASE_URL,
    LLM_CONTEXTUALIZATION_MODEL,
)
from retry import retry_with_backoff
from checkpoint import save_checkpoint, load_checkpoint


CHUNK_SETS_DIR = "runs/chunk_sets"


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
    2. First short non-empty line (<=120 chars, likely a title rather than a paragraph)
    3. Cleaned-up filename as fallback
    """
    first_short_line = None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if first_short_line is None and len(stripped) <= 120:
            first_short_line = stripped

    if first_short_line:
        return first_short_line

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
# Chunk Set I/O
# ============================================================================


def get_next_chunk_set_number() -> int:
    """Get the next chunk set number by checking existing directories."""
    os.makedirs(CHUNK_SETS_DIR, exist_ok=True)
    existing = glob.glob(os.path.join(CHUNK_SETS_DIR, "*"))

    run_numbers = []
    for path in existing:
        if not os.path.isdir(path):
            continue
        try:
            run_num = int(os.path.basename(path).split("_")[0])
            run_numbers.append(run_num)
        except (IndexError, ValueError):
            continue

    return max(run_numbers) + 1 if run_numbers else 1


def write_meta(meta_path: str, meta: dict):
    """Atomically write chunk set metadata."""
    tmp = meta_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, meta_path)


def append_chunk_line(jsonl_path: str, record: dict):
    """Append a single chunk record as a JSONL line."""
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_completed_chunks(jsonl_path: str) -> tuple[set[tuple[str, int]], dict[str, str]]:
    """Read a JSONL file and return completed chunks and cached summaries.

    Returns:
        Tuple of (set of (filename, chunk_index) pairs, dict of filename -> summary).
    """
    completed = set()
    summaries = {}
    if not os.path.exists(jsonl_path):
        return completed, summaries
    with open(jsonl_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                completed.add((record["filename"], record["chunk_index"]))
                # Cache the summary so we reuse the same one on resume
                if record["filename"] not in summaries and record.get("summary"):
                    summaries[record["filename"]] = record["summary"]
            except (json.JSONDecodeError, KeyError):
                continue
    return completed, summaries


# ============================================================================
# Main Chunking Pipeline
# ============================================================================


async def run_chunking(
    openai_client: AsyncOpenAI,
    documents: list[tuple[str, str]],
    chunk_size: int,
    chunk_set_dir: str | None = None,
    concurrency: int = 5,
) -> str:
    """
    Chunk all documents and write results to a chunk set directory.

    Args:
        openai_client: AsyncOpenAI client for LLM calls
        documents: List of (filename, content) tuples
        chunk_size: Character-level chunk size
        chunk_set_dir: Optional path to resume an existing chunk set.
                       If None, creates a new chunk set directory.
        concurrency: Max concurrent LLM calls (semaphore limit)

    Returns:
        Path to the chunk set directory.
    """
    if chunk_set_dir is None:
        num = get_next_chunk_set_number()
        timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")
        chunk_set_dir = os.path.join(CHUNK_SETS_DIR, f"{num}_{timestamp_str}")
        os.makedirs(chunk_set_dir, exist_ok=True)
        is_resuming = False
    else:
        is_resuming = True

    meta_path = os.path.join(chunk_set_dir, "meta.json")
    jsonl_path = os.path.join(chunk_set_dir, "chunks.jsonl")

    # Load existing progress if resuming
    if is_resuming:
        completed, cached_summaries = read_completed_chunks(jsonl_path)
        print(f"  Resuming: {len(completed)} chunks already done")
    else:
        completed = set()
        cached_summaries = {}

    # Write initial meta
    meta = {
        "chunk_set_number": int(os.path.basename(chunk_set_dir).split("_")[0]),
        "chunk_size": chunk_size,
        "status": "in_progress",
        "num_documents": len(documents),
        "num_chunks": len(completed),
        "timestamp": datetime.now().isoformat(),
        "documents": [f for f, _ in documents],
    }
    write_meta(meta_path, meta)

    semaphore = asyncio.Semaphore(concurrency)
    chunker = create_document_chunker(chunk_size)
    total_chunks = len(completed)

    print(f"  LLM concurrency: {concurrency}")

    for filename, content in documents:
        # Determine expected chunk count for this document
        if len(content) <= chunk_size:
            expected_chunks = {(filename, 0)}
        else:
            raw = chunker.chunk(content)
            expected_chunks = {(filename, i) for i in range(len(raw))}

        # Skip entirely if all chunks for this doc are done
        if expected_chunks.issubset(completed):
            print(f"\n  Skipping (fully done): {filename}")
            continue

        print(f"\n  Processing: {filename}")

        title = extract_document_title(filename, content)
        # Reuse cached summary from prior run to keep consistency across chunks
        if filename in cached_summaries:
            summary = cached_summaries[filename]
            print(f"  Title: {title}")
            print(f"  Summary (cached): {summary}")
        else:
            async with semaphore:
                summary = await summarize_document(openai_client, content, title)
            print(f"  Title: {title}")
            print(f"  Summary: {summary}")

        # Small documents: single chunk, no contextualization needed
        if len(content) <= chunk_size:
            record = {
                "filename": filename,
                "title": title,
                "summary": summary,
                "chunk_index": 0,
                "total_chunks": 1,
                "content": content,
                "context": None,
            }
            append_chunk_line(jsonl_path, record)
            total_chunks += 1
            meta["num_chunks"] = total_chunks
            write_meta(meta_path, meta)
            print(f"  ✓ Written as single chunk")
            continue

        # Chunk the document
        raw_chunks = chunker.chunk(content)
        chunks = [(i, c.text) for i, c in enumerate(raw_chunks)]
        pending_chunks = [(i, t) for i, t in chunks if (filename, i) not in completed]
        print(f"  Split into {len(chunks)} chunks ({len(chunks) - len(pending_chunks)} already done)")

        # Parallelize contextualization calls (bounded by semaphore)
        async def _contextualize_one(chunk_idx, chunk_txt, doc_content=content):
            chunk_label = f"chunk {chunk_idx + 1}/{len(chunks)} of '{filename}'"
            async with semaphore:
                ctx = await contextualize_chunk(
                    openai_client, doc_content, chunk_txt, chunk_label=chunk_label
                )
            return chunk_idx, chunk_txt, ctx

        results = await asyncio.gather(*[
            _contextualize_one(i, t) for i, t in pending_chunks
        ])

        # Write results in chunk-index order
        for chunk_index, chunk_text, chunk_context in results:
            record = {
                "filename": filename,
                "title": title,
                "summary": summary,
                "chunk_index": chunk_index,
                "total_chunks": len(chunks),
                "content": chunk_text,
                "context": chunk_context,
            }
            append_chunk_line(jsonl_path, record)
            total_chunks += 1
            meta["num_chunks"] = total_chunks
            write_meta(meta_path, meta)

        print(f"  ✓ All {len(chunks)} chunks written for {filename}")

    # Mark complete
    meta["status"] = "complete"
    meta["num_chunks"] = total_chunks
    write_meta(meta_path, meta)
    print(f"\n✓ Chunk set complete: {total_chunks} chunks written to {chunk_set_dir}")

    return chunk_set_dir


# ============================================================================
# CLI
# ============================================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zep Eval Harness — Document Chunking & Contextualization Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_chunk_documents.py                          # Chunk all docs with default size
  uv run zep_chunk_documents.py --chunk-size 1000        # Chunk with larger chunks
  uv run zep_chunk_documents.py --concurrency 10         # More parallel LLM calls
  uv run zep_chunk_documents.py --resume runs/chunk_sets/2_20260331T130000  # Resume
""",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
        help=f"Character-level chunk size (default: {CHUNK_SIZE})",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Path to a chunk set directory to resume (e.g., runs/chunk_sets/1_20260331T120000)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent LLM calls for summarization/contextualization (default: 5)",
    )
    return parser.parse_args()


async def main():
    load_dotenv()
    args = parse_args()

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        print("Error: Missing GOOGLE_API_KEY environment variable")
        print("   Required for document summarization and contextualization")
        exit(1)

    openai_client = AsyncOpenAI(api_key=google_api_key, base_url=GEMINI_BASE_URL)

    documents = load_documents()
    if not documents:
        print("Error: No documents found in data/documents/")
        exit(1)

    # Handle resume mode
    chunk_set_dir = None
    chunk_size = args.chunk_size
    if args.resume:
        if not os.path.isdir(args.resume):
            print(f"Error: Chunk set directory not found: {args.resume}")
            exit(1)
        meta_path = os.path.join(args.resume, "meta.json")
        if not os.path.exists(meta_path):
            print(f"Error: No meta.json in {args.resume}")
            exit(1)
        meta = load_checkpoint(meta_path)
        if meta.get("status") == "complete":
            print(f"Chunk set is already complete: {args.resume}")
            exit(0)
        chunk_size = meta.get("chunk_size", args.chunk_size)
        chunk_set_dir = args.resume
        print(f"✓ Resuming chunk set: {args.resume} (chunk_size={chunk_size})")

    print("=" * 80)
    print("ZEP DOCUMENT CHUNKING" + (" (RESUMING)" if chunk_set_dir else ""))
    print("=" * 80)
    print(f"  Chunk size: {chunk_size}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Documents: {len(documents)}")
    print("=" * 80)

    start = time()
    result_dir = await run_chunking(
        openai_client, documents, chunk_size,
        chunk_set_dir=chunk_set_dir,
        concurrency=args.concurrency,
    )
    elapsed = time() - start

    print(f"\n{'=' * 80}")
    print("CHUNKING COMPLETE")
    print(f"{'=' * 80}")
    print(f"  Chunk set: {result_dir}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"\nYou can now run:")
    print(f"  uv run zep_ingest_documents.py --chunk-set {os.path.basename(result_dir).split('_')[0]}")


if __name__ == "__main__":
    asyncio.run(main())
