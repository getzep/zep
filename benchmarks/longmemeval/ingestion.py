#!/usr/bin/env python3
"""
Ingestion module for LongMemEval benchmark
"""

import asyncio
import json
import os
import tarfile
from datetime import UTC, datetime
from typing import List

import gdown
import pandas as pd
from tqdm import tqdm
from zep_cloud import Message

from clients import create_openai_client, create_zep_client
from constants import (
    CHECKPOINT_FILE,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CONTEXTUALIZATION_MODEL,
    DATA_PATH,
    DEFAULT_CONCURRENCY,
    MAX_BATCH_SIZE,
    MAX_MESSAGE_SIZE,
)
from utils import setup_logging


class IngestionRunner:
    def __init__(
        self,
        log_level: str = "INFO",
        concurrency: int = DEFAULT_CONCURRENCY,
        checkpoint_file: str = CHECKPOINT_FILE,
    ):
        self.logger = setup_logging(log_level, __name__)

        # Initialize clients using factories
        self.zep = create_zep_client()
        self.oai_client = create_openai_client()

        # Concurrency controls
        self._semaphore = asyncio.Semaphore(concurrency)

        # Checkpoint tracking
        self.checkpoint_file = checkpoint_file
        self.checkpoint = self._load_checkpoint()

    def _load_checkpoint(self) -> dict:
        """Load checkpoint from file or create new one"""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file) as f:
                checkpoint = json.load(f)
                # Ensure failed_users exists for backwards compatibility
                if "failed_users" not in checkpoint:
                    checkpoint["failed_users"] = []
                return checkpoint
        return {
            "completed_users": [],
            "failed_users": [],
            "last_updated": None,
        }

    def _save_checkpoint(self):
        """Save current checkpoint to file"""
        self.checkpoint["last_updated"] = datetime.now(UTC).isoformat()
        os.makedirs(os.path.dirname(self.checkpoint_file), exist_ok=True)
        with open(self.checkpoint_file, "w") as f:
            json.dump(self.checkpoint, f, indent=2)
        self.logger.debug(
            f"Checkpoint saved: {len(self.checkpoint['completed_users'])} users completed"
        )

    async def _process_user(
        self,
        user_idx: int,
        df: pd.DataFrame,
    ) -> bool:
        """
        Process a single user's data with all their threads.
        Deletes user from Zep if any thread fails.

        Returns:
            bool: True if successful, False if failed
        """
        async with self._semaphore:  # Limit concurrent users
            # Get thread data from dataset
            user_threads = df["haystack_sessions"].iloc[user_idx]
            thread_dates = df["haystack_dates"].iloc[user_idx]
            question_type = df["question_type"][user_idx]

            self.logger.info(f"Processing user {user_idx}: {question_type}")

            # Initialize user
            user_id = f"lme_s_experiment_user_{user_idx}"

            # Attempt user creation
            try:
                await self.zep.user.add(user_id=user_id)
                self.logger.debug(f"User {user_id} created successfully")
            except Exception as e:
                self.logger.warning(f"User creation failed (may already exist): {e}")

            # Process each thread for this user
            for thread_idx, thread_messages in enumerate(user_threads):
                thread_id = f"lme_s_experiment_thread_{user_idx}_{thread_idx}"

                # Attempt thread creation
                try:
                    await self.zep.thread.create(user_id=user_id, thread_id=thread_id)
                    self.logger.debug(f"Thread {thread_id} created successfully")
                except Exception as e:
                    self.logger.error(f"Thread creation failed for {thread_id}: {e}")
                    # Delete user and mark as failed
                    await self._delete_user(user_id)
                    return False

                # Process messages in the thread
                if thread_messages:
                    try:
                        # Parse and format timestamp (same for all messages in this thread)
                        date = thread_dates[thread_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=UTC
                        )

                        # Process messages with size checking
                        messages = []
                        large_message_count = 0

                        for msg_idx, msg in enumerate(thread_messages):
                            # Check if message exceeds size limit
                            if len(msg["content"]) > MAX_MESSAGE_SIZE:
                                # Process large message via chunking pipeline
                                try:
                                    chunk_result = await self._process_large_message(
                                        msg,
                                        user_id,
                                        date_string.isoformat(),
                                        msg_idx,
                                    )
                                    large_message_count += 1
                                    self.logger.info(
                                        f"Processed large message {msg_idx} ({chunk_result['original_size']} chars) "
                                        f"into {chunk_result['chunks_created']} chunks, "
                                        f"{chunk_result['chunks_succeeded']} succeeded"
                                    )
                                except Exception as large_msg_e:
                                    self.logger.error(
                                        f"Failed to process large message {msg_idx}: {large_msg_e}"
                                    )
                                    raise  # Propagate to outer handler
                            else:
                                # Normal message processing
                                message_payload = Message(
                                    role=msg["role"],
                                    name=msg["role"],
                                    content=msg["content"],
                                    created_at=date_string.isoformat(),
                                )
                                messages.append(message_payload)

                        # Add normal-sized messages to Zep thread in batches
                        if messages:
                            for batch_start in range(0, len(messages), MAX_BATCH_SIZE):
                                batch_end = min(
                                    batch_start + MAX_BATCH_SIZE, len(messages)
                                )
                                batch = messages[batch_start:batch_end]

                                await self.zep.thread.add_messages(
                                    thread_id=thread_id,
                                    messages=batch,
                                )
                                self.logger.debug(
                                    f"Added batch {batch_start}-{batch_end} ({len(batch)} messages) to thread {thread_id}"
                                )

                        if large_message_count > 0:
                            self.logger.info(
                                f"Thread {thread_id}: {len(messages)} normal messages, "
                                f"{large_message_count} large messages processed via graph.add"
                            )

                    except Exception as e:
                        self.logger.error(
                            f"Failed to process messages for thread {thread_id}: {e}"
                        )
                        # Delete user and mark as failed
                        await self._delete_user(user_id)
                        return False

            self.logger.info(
                f"Completed processing user {user_idx} ({len(user_threads)} threads)"
            )
            return True

    async def _chunk_large_message(self, content: str) -> List[str]:
        """Split large content into overlapping chunks using paragraph-aware strategy"""
        if len(content) <= CHUNK_SIZE:
            return [content]

        chunks = []
        start = 0

        while start < len(content):
            # Calculate chunk end position
            end = min(start + CHUNK_SIZE, len(content))

            # If this is the last chunk, take everything remaining
            if end >= len(content):
                chunks.append(content[start:])
                break

            # Try to find a good break point near the end to avoid cutting mid-sentence/paragraph
            chunk_text = content[start:end]

            # Look for paragraph break (ideal)
            last_para_break = chunk_text.rfind("\n\n")
            if (
                last_para_break > len(chunk_text) // 2
            ):  # Only if it's past halfway point
                end = start + last_para_break + 2
            else:
                # Look for sentence break (good)
                last_sentence_break = chunk_text.rfind(". ")
                if last_sentence_break > len(chunk_text) // 2:
                    end = start + last_sentence_break + 2
                else:
                    # Look for line break (acceptable)
                    last_line_break = chunk_text.rfind("\n")
                    if last_line_break > len(chunk_text) // 2:
                        end = start + last_line_break + 1
                    # Otherwise use hard boundary at CHUNK_SIZE

            # Add the chunk
            chunks.append(content[start:end])

            # Next chunk starts with overlap
            start = max(end - CHUNK_OVERLAP, start + 1)  # Ensure progress

        return chunks

    async def _contextualize_chunk(self, full_document: str, chunk: str) -> str:
        """Generate context sentence using OpenAI for chunk relationship to document"""
        try:
            prompt = f"""Given this long chat message or document and a specific chunk from it, generate a single sentence that:
1. Briefly describes what the overall message/document is about
2. Explains how this specific chunk relates to or fits within the larger message/document

Full document (first 5000 chars): {full_document[:5000]}

Specific chunk: {chunk[:1000]}

Respond with only a single sentence that provides context for this chunk within the larger document."""

            response = await self.oai_client.chat.completions.create(
                model=CONTEXTUALIZATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=100,
            )

            context_sentence = response.choices[0].message.content or ""
            return context_sentence.strip()

        except Exception as e:
            self.logger.warning(f"Failed to contextualize chunk: {e}")
            return "This chunk is part of a larger document."

    async def _process_large_message(
        self, msg: dict, user_id: str, date_string: str, msg_idx: int
    ) -> dict:
        """Handle large messages via chunking → contextualization → graph.add"""
        result = {
            "original_size": len(msg["content"]),
            "chunks_created": 0,
            "chunks_succeeded": 0,
            "chunking_error": "",
        }

        try:
            # Chunk the large message
            chunks = await self._chunk_large_message(msg["content"])
            result["chunks_created"] = len(chunks)

            self.logger.debug(f"Split message {msg_idx} into {len(chunks)} chunks")

            # Process each chunk
            for chunk_idx, chunk in enumerate(chunks):
                try:
                    # Generate context for this chunk
                    context = await self._contextualize_chunk(msg["content"], chunk)

                    # Format as message with context
                    contextualized_content = f"Context: {context}\nContent: {chunk}"

                    # Create the final graph data
                    graph_data = (
                        f"{msg['role']} ({date_string}): {contextualized_content}"
                    )

                    # Add to graph using graph.add API
                    await self.zep.graph.add(
                        user_id=user_id,
                        type="text",
                        data=graph_data,
                    )

                    result["chunks_succeeded"] += 1
                    self.logger.debug(
                        f"Successfully added chunk {chunk_idx + 1}/{len(chunks)} for message {msg_idx} "
                        f"(size: {len(graph_data)} chars)"
                    )

                except Exception as chunk_e:
                    self.logger.warning(
                        f"Failed to process chunk {chunk_idx + 1}/{len(chunks)} for message {msg_idx}: {chunk_e}"
                    )
                    if not result["chunking_error"]:
                        result["chunking_error"] = (
                            f"Chunk {chunk_idx + 1} failed: {str(chunk_e)}"
                        )

        except Exception as e:
            result["chunking_error"] = str(e)
            self.logger.error(f"Failed to chunk message {msg_idx}: {e}")

        return result

    async def _delete_user(self, user_id: str):
        """Delete a user from Zep (cleanup on failure)"""
        try:
            await self.zep.user.delete(user_id=user_id)
            self.logger.info(f"Deleted user {user_id} due to ingestion failure")
        except Exception as e:
            self.logger.warning(f"Failed to delete user {user_id}: {e}")

    async def download_dataset(
        self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")
    ):
        """Download and extract the LongMemEval dataset from Google Drive"""
        file_id = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"

        os.makedirs(DATA_PATH, exist_ok=True)

        # Skip download if file already exists
        if os.path.exists(file_path):
            self.logger.info(f"Dataset already downloaded at {file_path}")
        else:
            self.logger.info("Downloading LongMemEval dataset...")
            url = f"https://drive.google.com/uc?id={file_id}"
            gdown.download(url, file_path, quiet=False)
            self.logger.info("Download complete")

        # Extract dataset
        self.logger.info("Extracting dataset...")
        with tarfile.open(file_path, "r:gz") as tar:
            tar.extractall(path=DATA_PATH)
        self.logger.info(f"Dataset extracted to {DATA_PATH}")

    async def ingest_data(
        self,
        df: pd.DataFrame,
        num_users: int = 500,
        continue_from_checkpoint: bool = False,
    ):
        """
        Ingest LongMemEval data into Zep with checkpoint support

        Args:
            df: DataFrame containing LongMemEval data
            num_users: Number of users to ingest (default: 500)
                      - Without --continue: total users to ingest (0 to num_users-1)
                      - With --continue: additional users to process beyond checkpoint
            continue_from_checkpoint: Continue from previous checkpoint if True
        """
        max_users = len(df)

        # Determine starting point
        if continue_from_checkpoint:
            completed_users = set(self.checkpoint.get("completed_users", []))
            failed_users = set(self.checkpoint.get("failed_users", []))
            already_processed = len(completed_users) + len(failed_users)

            # Calculate how many more users to process
            target_count = min(already_processed + num_users, max_users)

            users_to_process = [
                idx
                for idx in range(target_count)
                if idx not in completed_users and idx not in failed_users
            ]
            print(
                f"Resuming from checkpoint: {len(completed_users)} completed, "
                f"{len(failed_users)} failed, {len(users_to_process)} to process"
            )
        else:
            # Fresh start - reset checkpoint
            self.checkpoint = {
                "completed_users": [],
                "failed_users": [],
                "last_updated": None,
            }
            self._save_checkpoint()
            actual_users = min(num_users, max_users)
            users_to_process = list(range(actual_users))
            print(f"Starting fresh ingestion for {actual_users} users")

        if not users_to_process:
            print("No users to process (all already completed or failed)")
            return

        # Process users with progress bar
        with tqdm(
            total=len(users_to_process), desc="Ingesting users", unit="user"
        ) as pbar:
            for user_idx in users_to_process:
                # Process user
                success = await self._process_user(user_idx, df)

                # Update checkpoint
                if success:
                    self.checkpoint["completed_users"].append(user_idx)
                    pbar.set_postfix({"status": "✓", "user": user_idx})
                else:
                    self.checkpoint["failed_users"].append(user_idx)
                    pbar.set_postfix({"status": "✗", "user": user_idx})

                # Save checkpoint after each user
                self._save_checkpoint()
                pbar.update(1)

        print(
            f"\nIngestion completed: {len(users_to_process)} users processed "
            f"({len(self.checkpoint['completed_users'])} total completed, "
            f"{len(self.checkpoint['failed_users'])} total failed)"
        )
