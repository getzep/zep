#!/usr/bin/env python3
"""
Ingestion module for LongMemEval benchmark
"""

import asyncio
import json
import os
import tarfile
from datetime import UTC, datetime

import gdown
import pandas as pd
from tqdm import tqdm
from zep_cloud import Message

from clients import create_zep_client
from constants import CHECKPOINT_FILE, DATA_PATH, DEFAULT_CONCURRENCY
from utils import setup_logging


class IngestionRunner:
    def __init__(
        self,
        log_level: str = "INFO",
        concurrency: int = DEFAULT_CONCURRENCY,
        checkpoint_file: str = CHECKPOINT_FILE,
    ):
        self.logger = setup_logging(log_level, __name__)

        # Initialize Zep client using factory
        self.zep = create_zep_client()

        # Concurrency controls
        self._semaphore = asyncio.Semaphore(concurrency)

        # Checkpoint tracking
        self.checkpoint_file = checkpoint_file
        self.checkpoint = self._load_checkpoint()

    def _load_checkpoint(self) -> dict:
        """Load checkpoint from file or create new one"""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file) as f:
                return json.load(f)
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
                        date_string = datetime.strptime(date, date_format).replace(tzinfo=UTC)

                        # Create message payloads
                        messages = []
                        for msg in thread_messages:
                            message_payload = Message(
                                role=msg["role"],
                                name=msg["role"],
                                content=msg["content"],
                                created_at=date_string.isoformat(),
                            )
                            messages.append(message_payload)

                        # Add messages to Zep thread
                        await self.zep.thread.add_messages(
                            thread_id=thread_id,
                            messages=messages,
                        )

                        self.logger.debug(f"Added {len(messages)} messages to thread {thread_id}")

                    except Exception as e:
                        self.logger.error(f"Failed to process messages for thread {thread_id}: {e}")
                        # Delete user and mark as failed
                        await self._delete_user(user_id)
                        return False

            self.logger.info(f"Completed processing user {user_idx} ({len(user_threads)} threads)")
            return True

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
            completed_users = set(self.checkpoint["completed_users"])
            failed_users = set(self.checkpoint["failed_users"])
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
        with tqdm(total=len(users_to_process), desc="Ingesting users", unit="user") as pbar:
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
