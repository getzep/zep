#!/usr/bin/env python3
"""
Document Chunking with Contextualized Retrieval for Zep

This script demonstrates Anthropic's contextualized retrieval technique:
1. Chunks a document into manageable pieces
2. Uses OpenAI (gpt-4o-mini) to generate context for each chunk
3. Ingests contextualized chunks into Zep's knowledge graph

The contextualization step improves retrieval accuracy by situating each
chunk within the broader document context.
"""

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class ChunkingConfig:
    """Configuration for document chunking."""
    chunk_size: int = 6000  # Characters per chunk (leaves room for context within 10K limit)
    chunk_overlap: int = 200  # Overlap between chunks for continuity
    max_total_size: int = 10000  # Zep's maximum episode size


@dataclass
class ProcessingStats:
    """Statistics for tracking processing progress."""
    total_chunks: int = 0
    processed_chunks: int = 0
    failed_chunks: int = 0
    total_characters: int = 0
    contextualized_characters: int = 0


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using common delimiters."""
    # Split on sentence-ending punctuation followed by space or newline
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs."""
    paragraphs = re.split(r'\n\n+', text)
    return [p.strip() for p in paragraphs if p.strip()]


def chunk_document(document: str, config: ChunkingConfig) -> list[str]:
    """
    Chunk a document into smaller pieces suitable for processing.

    Strategy:
    1. First split by paragraphs
    2. If a paragraph is too large, split by sentences
    3. Combine small paragraphs/sentences until chunk_size is reached
    4. Maintain overlap between chunks for continuity
    """
    chunks = []
    paragraphs = split_into_paragraphs(document)

    current_chunk = ""

    for paragraph in paragraphs:
        # If paragraph is too large, split into sentences
        if len(paragraph) > config.chunk_size:
            sentences = split_into_sentences(paragraph)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= config.chunk_size:
                    current_chunk = f"{current_chunk} {sentence}".strip() if current_chunk else sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                        # Create overlap from the end of the current chunk
                        overlap_text = current_chunk[-config.chunk_overlap:] if len(current_chunk) > config.chunk_overlap else current_chunk
                        current_chunk = f"{overlap_text} {sentence}".strip()
                    else:
                        # Sentence itself is too large, truncate it
                        chunks.append(sentence[:config.chunk_size])
                        current_chunk = ""
        else:
            # Try to add paragraph to current chunk
            if len(current_chunk) + len(paragraph) + 2 <= config.chunk_size:
                current_chunk = f"{current_chunk}\n\n{paragraph}".strip() if current_chunk else paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                    # Create overlap from the end of the current chunk
                    overlap_text = current_chunk[-config.chunk_overlap:] if len(current_chunk) > config.chunk_overlap else current_chunk
                    current_chunk = f"{overlap_text}\n\n{paragraph}".strip()
                else:
                    current_chunk = paragraph

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def contextualize_chunk(openai_client, full_document: str, chunk: str, chunk_index: int, total_chunks: int) -> str:
    """
    Use gpt-4o-mini to situate a chunk within the document context.

    This implements Anthropic's contextualized retrieval technique,
    which improves search retrieval by adding contextual information
    to each chunk.
    """
    prompt = f"""<document>
{full_document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the
overall document for the purposes of improving search retrieval of the
chunk. If the document has a publication date, please include the date
in your context. Answer only with the succinct context and nothing else."""

    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=0.0
            )
            context = response.choices[0].message.content.strip()
            return f"{context}\n\n---\n\n{chunk}"

        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < max_retries - 1:
                print(f"  Rate limited, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            elif attempt < max_retries - 1:
                print(f"  Error contextualizing chunk {chunk_index + 1}/{total_chunks}: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def validate_and_truncate_chunk(contextualized_chunk: str, max_size: int) -> str:
    """
    Validate chunk size and truncate if necessary.

    Zep has a 10K character limit for episodes. If the contextualized
    chunk exceeds this, we truncate the context portion while preserving
    the original chunk content.
    """
    if len(contextualized_chunk) <= max_size:
        return contextualized_chunk

    # Find the separator between context and chunk
    separator = "\n\n---\n\n"
    separator_idx = contextualized_chunk.find(separator)

    if separator_idx == -1:
        # No separator found, just truncate
        return contextualized_chunk[:max_size]

    context = contextualized_chunk[:separator_idx]
    chunk = contextualized_chunk[separator_idx + len(separator):]

    # Calculate how much we need to truncate from the context
    total_overhead = len(separator) + len(chunk)
    max_context_size = max_size - total_overhead

    if max_context_size <= 0:
        # Chunk itself is too large, truncate it
        return chunk[:max_size]

    truncated_context = context[:max_context_size]
    return f"{truncated_context}{separator}{chunk}"


def ensure_user_exists(zep_client, user_id: str) -> bool:
    """
    Ensure a user exists in Zep, creating them if necessary.

    Returns True if user exists or was created successfully.
    """
    try:
        # Try to get the user first
        zep_client.user.get(user_id)
        print(f"  User '{user_id}' exists")
        return True
    except Exception as e:
        if "404" in str(e) or "not found" in str(e).lower():
            # User doesn't exist, create them
            print(f"  User '{user_id}' not found, creating...")
            try:
                zep_client.user.add(user_id=user_id)
                print(f"  User '{user_id}' created successfully")
                return True
            except Exception as create_err:
                print(f"  ERROR creating user: {create_err}")
                return False
        else:
            print(f"  ERROR checking user: {e}")
            return False


def ingest_to_zep(zep_client, user_id: str, contextualized_chunk: str, chunk_index: int, total_chunks: int, wait: bool = False) -> Optional[str]:
    """
    Ingest a contextualized chunk into Zep's knowledge graph.

    Uses the graph.add() method to add the chunk as an episode,
    which will be processed and added to the user's knowledge graph.
    """
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            episode = zep_client.graph.add(
                user_id=user_id,
                type="text",
                data=contextualized_chunk
            )

            episode_uuid = episode.uuid if hasattr(episode, 'uuid') else str(episode)

            if wait and hasattr(episode, 'uuid'):
                print(f"  Waiting for episode {episode_uuid} to be processed...")
                # Poll for completion (simple implementation)
                # In production, you might want to use webhooks or async processing
                time.sleep(2)

            return episode_uuid

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Error ingesting chunk {chunk_index + 1}/{total_chunks}: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def process_document(
    document_path: str,
    user_id: str,
    config: ChunkingConfig,
    wait: bool = False,
    dry_run: bool = False
) -> ProcessingStats:
    """
    Process a document through the full pipeline:
    1. Read and chunk the document
    2. Contextualize each chunk using OpenAI
    3. Ingest each contextualized chunk into Zep
    """
    # Initialize clients
    openai_api_key = os.getenv("OPENAI_API_KEY")
    zep_api_key = os.getenv("ZEP_API_KEY")

    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    if not zep_api_key and not dry_run:
        raise ValueError("ZEP_API_KEY environment variable not set")

    from openai import OpenAI
    openai_client = OpenAI(api_key=openai_api_key)

    zep_client = None
    if not dry_run:
        from zep_cloud.client import Zep
        zep_client = Zep(api_key=zep_api_key)

        # Ensure user exists before processing
        print(f"\nChecking user: {user_id}")
        if not ensure_user_exists(zep_client, user_id):
            raise ValueError(f"Failed to ensure user '{user_id}' exists in Zep")

    # Read document
    print(f"\nReading document: {document_path}")
    document_content = Path(document_path).read_text(encoding='utf-8')
    print(f"Document size: {len(document_content):,} characters")

    # Chunk document
    print(f"\nChunking document (chunk_size={config.chunk_size}, overlap={config.chunk_overlap})...")
    chunks = chunk_document(document_content, config)
    print(f"Created {len(chunks)} chunks")

    # Initialize stats
    stats = ProcessingStats(
        total_chunks=len(chunks),
        total_characters=len(document_content)
    )

    # Process each chunk
    print("\nProcessing chunks:")
    print("-" * 60)

    for i, chunk in enumerate(chunks):
        print(f"\nChunk {i + 1}/{len(chunks)} ({len(chunk):,} chars)")

        # Contextualize
        print("  Contextualizing with OpenAI...")
        try:
            contextualized = contextualize_chunk(
                openai_client,
                document_content,
                chunk,
                i,
                len(chunks)
            )

            # Validate and truncate if needed
            original_size = len(contextualized)
            contextualized = validate_and_truncate_chunk(contextualized, config.max_total_size)
            if len(contextualized) < original_size:
                print(f"  Truncated from {original_size:,} to {len(contextualized):,} chars")

            stats.contextualized_characters += len(contextualized)

            # Preview the context
            context_end = contextualized.find("\n\n---\n\n")
            if context_end > 0:
                context_preview = contextualized[:min(context_end, 100)]
                print(f"  Context: \"{context_preview}...\"")

        except Exception as e:
            print(f"  ERROR contextualizing: {e}")
            stats.failed_chunks += 1
            continue

        # Ingest to Zep
        if dry_run:
            print("  [DRY RUN] Would ingest to Zep")
            stats.processed_chunks += 1
        else:
            print("  Ingesting to Zep...")
            try:
                episode_uuid = ingest_to_zep(
                    zep_client,
                    user_id,
                    contextualized,
                    i,
                    len(chunks),
                    wait=wait
                )
                print(f"  Created episode: {episode_uuid}")
                stats.processed_chunks += 1
            except Exception as e:
                print(f"  ERROR ingesting: {e}")
                stats.failed_chunks += 1

    return stats


def print_summary(stats: ProcessingStats):
    """Print a summary of the processing results."""
    print("\n" + "=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    print(f"Total chunks: {stats.total_chunks}")
    print(f"Successfully processed: {stats.processed_chunks}")
    print(f"Failed: {stats.failed_chunks}")
    print(f"Original document size: {stats.total_characters:,} characters")
    print(f"Total contextualized size: {stats.contextualized_characters:,} characters")
    if stats.total_characters > 0:
        expansion = (stats.contextualized_characters / stats.total_characters - 1) * 100
        print(f"Size expansion from contextualization: {expansion:.1f}%")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Chunk and ingest documents into Zep with contextualized retrieval",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python chunk_and_ingest.py sample_document.txt --user-id user123

  # Custom chunk size
  python chunk_and_ingest.py doc.txt --user-id user123 --chunk-size 4000

  # Wait for processing
  python chunk_and_ingest.py doc.txt --user-id user123 --wait

  # Dry run (test without ingesting)
  python chunk_and_ingest.py doc.txt --user-id user123 --dry-run
        """
    )

    parser.add_argument(
        "document",
        help="Path to the document to process"
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="Zep user ID to associate the chunks with"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=6000,
        help="Maximum characters per chunk (default: 6000)"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=200,
        help="Character overlap between chunks (default: 200)"
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for each episode to be processed before continuing"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and contextualize but don't ingest to Zep"
    )

    args = parser.parse_args()

    # Validate document exists
    if not Path(args.document).exists():
        print(f"Error: Document not found: {args.document}", file=sys.stderr)
        sys.exit(1)

    # Create config
    config = ChunkingConfig(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap
    )

    # Process
    print("=" * 60)
    print("DOCUMENT CHUNKING WITH CONTEXTUALIZED RETRIEVAL")
    print("=" * 60)
    print(f"Document: {args.document}")
    print(f"User ID: {args.user_id}")
    print(f"Chunk size: {config.chunk_size}")
    print(f"Chunk overlap: {config.chunk_overlap}")
    print(f"Dry run: {args.dry_run}")

    try:
        stats = process_document(
            args.document,
            args.user_id,
            config,
            wait=args.wait,
            dry_run=args.dry_run
        )
        print_summary(stats)

        if stats.failed_chunks > 0:
            sys.exit(1)

    except Exception as e:
        print(f"\nFatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
