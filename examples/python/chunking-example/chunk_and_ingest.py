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

import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI
from zep_cloud.client import Zep

# Load environment variables
load_dotenv()

# Configuration
CHUNK_SIZE = 500  # Characters per chunk
CHUNK_OVERLAP = 50  # Overlap between chunks for continuity
ZEP_MAX_EPISODE_SIZE = 10000  # Zep's maximum episode size
OPENAI_MODEL = "gpt-5-mini-2025-08-07"


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using common delimiters."""
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs."""
    paragraphs = re.split(r'\n\n+', text)
    return [p.strip() for p in paragraphs if p.strip()]


def chunk_document(document: str) -> list[str]:
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
        if len(paragraph) > CHUNK_SIZE:
            sentences = split_into_sentences(paragraph)
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= CHUNK_SIZE:
                    current_chunk = f"{current_chunk} {sentence}".strip() if current_chunk else sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                        overlap_text = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else current_chunk
                        current_chunk = f"{overlap_text} {sentence}".strip()
                    else:
                        chunks.append(sentence[:CHUNK_SIZE])
                        current_chunk = ""
        else:
            if len(current_chunk) + len(paragraph) + 2 <= CHUNK_SIZE:
                current_chunk = f"{current_chunk}\n\n{paragraph}".strip() if current_chunk else paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                    overlap_text = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else current_chunk
                    current_chunk = f"{overlap_text}\n\n{paragraph}".strip()
                else:
                    current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def contextualize_chunk(openai_client: OpenAI, full_document: str, chunk: str) -> str:
    """
    Use OpenAI to situate a chunk within the document context.

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
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=256
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
    """
    Validate chunk size and truncate if necessary.

    Zep has a 10K character limit for episodes. If the contextualized
    chunk exceeds this, we truncate the context portion while preserving
    the original chunk content.
    """
    if len(contextualized_chunk) <= ZEP_MAX_EPISODE_SIZE:
        return contextualized_chunk

    separator = "\n\n---\n\n"
    separator_idx = contextualized_chunk.find(separator)

    if separator_idx == -1:
        return contextualized_chunk[:ZEP_MAX_EPISODE_SIZE]

    context = contextualized_chunk[:separator_idx]
    chunk = contextualized_chunk[separator_idx + len(separator):]

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
        else:
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
                data=contextualized_chunk
            )
            return episode.uuid if hasattr(episode, 'uuid') else str(episode)

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Error ingesting: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def process_document(document_path: str, user_id: str):
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
    if not zep_api_key:
        raise ValueError("ZEP_API_KEY environment variable not set")

    openai_client = OpenAI(api_key=openai_api_key)
    zep_client = Zep(api_key=zep_api_key)

    # Ensure user exists
    print(f"\nChecking user: {user_id}")
    if not ensure_user_exists(zep_client, user_id):
        raise ValueError(f"Failed to ensure user '{user_id}' exists in Zep")

    # Read document
    print(f"\nReading document: {document_path}")
    with open(document_path, 'r', encoding='utf-8') as f:
        document_content = f.read()
    print(f"Document size: {len(document_content):,} characters")

    # Chunk document
    print(f"\nChunking document (chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})...")
    chunks = chunk_document(document_content)
    print(f"Created {len(chunks)} chunks")

    # Process each chunk
    print("\nProcessing chunks:")
    print("-" * 60)

    for i, chunk in enumerate(chunks):
        print(f"\nChunk {i + 1}/{len(chunks)} ({len(chunk):,} chars)")

        # Contextualize
        print("  Contextualizing with OpenAI...")
        try:
            contextualized = contextualize_chunk(openai_client, document_content, chunk)
            contextualized = validate_and_truncate_chunk(contextualized)

            # Preview the context
            context_end = contextualized.find("\n\n---\n\n")
            if context_end > 0:
                context_preview = contextualized[:min(context_end, 100)]
                print(f"  Context: \"{context_preview}...\"")

        except Exception as e:
            print(f"  ERROR contextualizing: {e}")
            continue

        # Ingest to Zep
        print("  Ingesting to Zep...")
        try:
            episode_uuid = ingest_to_zep(zep_client, user_id, contextualized)
            print(f"  Created episode: {episode_uuid}")
        except Exception as e:
            print(f"  ERROR ingesting: {e}")

    print("\n" + "=" * 60)
    print("Processing complete!")
    print("=" * 60)


if __name__ == "__main__":
    # Example usage - modify these values as needed
    DOCUMENT_PATH = "sample_document.txt"
    USER_ID = "example-user"

    process_document(DOCUMENT_PATH, USER_ID)
