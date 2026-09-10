#!/usr/bin/env python3
"""
Document Chunking with Contextualized Retrieval for Zep

This script demonstrates Anthropic's contextualized retrieval technique:
1. Chunks a document into manageable pieces
2. Uses OpenAI to generate context for each chunk
3. Ingests contextualized chunks into Zep's knowledge graph

The contextualization step improves retrieval accuracy by situating each
chunk within the broader document context.
"""

from __future__ import annotations

import argparse
import os
import re
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DEFAULT_CHUNK_SIZE = 6000  # Characters per chunk
DEFAULT_CHUNK_OVERLAP = 200  # Overlap between chunks for continuity
ZEP_MAX_EPISODE_SIZE = 10000  # Zep's maximum episode size
OPENAI_MODEL = "gpt-4o-mini"


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using common delimiters."""
    sentence_pattern = r"(?<=[.!?])\s+"
    sentences = re.split(sentence_pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs."""
    paragraphs = re.split(r"\n\n+", text)
    return [p.strip() for p in paragraphs if p.strip()]


def chunk_document(
    document: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """
    Chunk a document into smaller pieces suitable for processing.

    Strategy:
    1. First split by paragraphs
    2. If a paragraph is too large, split by sentences
    3. Combine small paragraphs/sentences until chunk_size is reached
    4. Maintain overlap between chunks for continuity
    """
    chunks: list[str] = []
    paragraphs = split_into_paragraphs(document)
    current_chunk = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            sentences = split_into_sentences(paragraph)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= chunk_size:
                    current_chunk = (
                        f"{current_chunk} {sentence}".strip()
                        if current_chunk
                        else sentence
                    )
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                        overlap_text = (
                            current_chunk[-chunk_overlap:]
                            if len(current_chunk) > chunk_overlap
                            else current_chunk
                        )
                        current_chunk = f"{overlap_text} {sentence}".strip()
                    else:
                        chunks.append(sentence[:chunk_size])
                        current_chunk = ""
        else:
            if len(current_chunk) + len(paragraph) + 2 <= chunk_size:
                current_chunk = (
                    f"{current_chunk}\n\n{paragraph}".strip()
                    if current_chunk
                    else paragraph
                )
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                    overlap_text = (
                        current_chunk[-chunk_overlap:]
                        if len(current_chunk) > chunk_overlap
                        else current_chunk
                    )
                    current_chunk = f"{overlap_text}\n\n{paragraph}".strip()
                else:
                    current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def contextualize_chunk(openai_client: OpenAI, full_document: str, chunk: str) -> str:
    """Use OpenAI to situate a chunk within the document context."""
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
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=256,
            )
            context = response.choices[0].message.content.strip()
            return f"{context}\n\n---\n\n{chunk}"
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < max_retries - 1:
                print(f"  Rate limited, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            elif attempt < max_retries - 1:
                print(f"  Error contextualizing: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def validate_and_truncate_chunk(contextualized_chunk: str) -> str:
    """Validate chunk size and truncate context if necessary."""
    if len(contextualized_chunk) <= ZEP_MAX_EPISODE_SIZE:
        return contextualized_chunk

    separator = "\n\n---\n\n"
    separator_idx = contextualized_chunk.find(separator)

    if separator_idx == -1:
        return contextualized_chunk[:ZEP_MAX_EPISODE_SIZE]

    context = contextualized_chunk[:separator_idx]
    chunk = contextualized_chunk[separator_idx + len(separator) :]

    total_overhead = len(separator) + len(chunk)
    max_context_size = ZEP_MAX_EPISODE_SIZE - total_overhead

    if max_context_size <= 0:
        return chunk[:ZEP_MAX_EPISODE_SIZE]

    truncated_context = context[:max_context_size]
    return f"{truncated_context}{separator}{chunk}"


def ensure_user_exists(zep_client: Zep, user_id: str) -> bool:
    """Ensure a user exists in Zep, creating them if necessary."""
    try:
        zep_client.user.get(user_id)
        print(f"User '{user_id}' exists")
        return True
    except Exception as e:
        if "404" in str(e) or "not found" in str(e).lower():
            print(f"User '{user_id}' not found, creating...")
            try:
                zep_client.user.add(user_id=user_id)
                print(f"User '{user_id}' created successfully")
                return True
            except Exception as create_err:
                print(f"ERROR creating user: {create_err}")
                return False
        print(f"ERROR checking user: {e}")
        return False


def ingest_to_zep(zep_client: Zep, user_id: str, contextualized_chunk: str) -> str | None:
    """Ingest a contextualized chunk into Zep's knowledge graph."""
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            episode = zep_client.graph.add(
                user_id=user_id,
                type="text",
                data=contextualized_chunk,
            )
            return episode.uuid_ if hasattr(episode, "uuid_") else getattr(episode, "uuid", str(episode))
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Error ingesting: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def process_document(
    document_path: str,
    user_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    dry_run: bool = False,
    wait: bool = False,
) -> None:
    """Process a document through chunking, contextualization, and ingestion."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    zep_api_key = os.getenv("ZEP_API_KEY")

    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    if not dry_run and not zep_api_key:
        raise ValueError("ZEP_API_KEY environment variable not set (or pass --dry-run)")

    from openai import OpenAI
    from zep_cloud.client import Zep

    openai_client = OpenAI(api_key=openai_api_key)
    zep_client = Zep(api_key=zep_api_key) if (zep_api_key and not dry_run) else None

    print("=" * 60)
    print("DOCUMENT CHUNKING WITH CONTEXTUALIZED RETRIEVAL")
    print("=" * 60)
    print(f"Document: {document_path}")
    print(f"User ID: {user_id}")
    print(f"Chunk size: {chunk_size}")
    print(f"Chunk overlap: {chunk_overlap}")
    print(f"Dry run: {dry_run}")
    print(f"Wait: {wait}")

    if not dry_run:
        print(f"\nChecking user: {user_id}")
        if not ensure_user_exists(zep_client, user_id):
            raise ValueError(f"Failed to ensure user '{user_id}' exists in Zep")

    print(f"\nReading document: {document_path}")
    with open(document_path, "r", encoding="utf-8") as f:
        document_content = f.read()
    print(f"Document size: {len(document_content):,} characters")

    print(
        f"\nChunking document (chunk_size={chunk_size}, overlap={chunk_overlap})..."
    )
    chunks = chunk_document(
        document_content, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    print(f"Created {len(chunks)} chunks")

    print("\nProcessing chunks:")
    print("-" * 60)

    success = 0
    failed = 0
    contextualized_size = 0

    for i, chunk in enumerate(chunks):
        print(f"\nChunk {i + 1}/{len(chunks)} ({len(chunk):,} chars)")
        print("  Contextualizing with OpenAI...")
        try:
            contextualized = contextualize_chunk(
                openai_client, document_content, chunk
            )
            contextualized = validate_and_truncate_chunk(contextualized)
            contextualized_size += len(contextualized)
            context_end = contextualized.find("\n\n---\n\n")
            if context_end > 0:
                context_preview = contextualized[: min(context_end, 100)]
                print(f'  Context: "{context_preview}..."')
        except Exception as e:
            print(f"  ERROR contextualizing: {e}")
            failed += 1
            continue

        if dry_run:
            print("  Dry run — skipping Zep ingestion")
            success += 1
            continue

        print("  Ingesting to Zep...")
        try:
            episode_uuid = ingest_to_zep(zep_client, user_id, contextualized)
            print(f"  Created episode: {episode_uuid}")
            success += 1
            if wait:
                print("  Waiting for episode processing...")
                time.sleep(2)
        except Exception as e:
            print(f"  ERROR ingesting: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    print(f"Total chunks: {len(chunks)}")
    print(f"Successfully processed: {success}")
    print(f"Failed: {failed}")
    print(f"Original document size: {len(document_content):,} characters")
    print(f"Total contextualized size: {contextualized_size:,} characters")
    if document_content:
        expansion = (
            (contextualized_size - len(document_content)) / len(document_content)
        ) * 100
        print(f"Size expansion from contextualization: {expansion:.1f}%")
    print("=" * 60)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Chunk a document, contextualize each chunk with OpenAI, and ingest into Zep"
        )
    )
    parser.add_argument("document", help="Path to the document to process")
    parser.add_argument(
        "--user-id",
        required=True,
        help="Zep user ID for the knowledge graph",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Maximum characters per chunk (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help=f"Character overlap between chunks (default: {DEFAULT_CHUNK_OVERLAP})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process without ingesting to Zep",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for processing after each chunk",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    process_document(
        document_path=args.document,
        user_id=args.user_id,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        dry_run=args.dry_run,
        wait=args.wait,
    )
