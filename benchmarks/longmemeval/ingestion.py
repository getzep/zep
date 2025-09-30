#!/usr/bin/env python3
"""
Ingestion module for LongMemEval benchmark
"""

import asyncio
import logging
import os
import tarfile
from datetime import UTC, datetime

import gdown
import pandas as pd
from zep_cloud import Message

from clients import create_zep_client

DATA_PATH = "data"


class IngestionRunner:
    def __init__(
        self,
        log_level: str = "INFO",
    ):
        self.logger = self._setup_logging(log_level)

        # Initialize Zep client using factory
        self.zep = create_zep_client()

        # Concurrency controls
        self._semaphore = asyncio.Semaphore(2)  # Limit to 2 concurrent users

    def _setup_logging(self, log_level: str) -> logging.Logger:
        """Configure logging with proper formatting"""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(getattr(logging, log_level.upper()))
        return logger

    async def _process_user(
        self,
        user_idx: int,
        df: pd.DataFrame,
    ):
        """Process a single user's data with all their threads"""
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
                    self.logger.warning(f"Thread creation failed (may already exist): {e}")
                    continue

                # Process messages in the thread
                if thread_messages:
                    try:
                        # Parse and format timestamp (same for all messages in this thread)
                        date = thread_dates[thread_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=UTC
                        )

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

            self.logger.info(f"Completed processing user {user_idx}")

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
    ):
        """
        Ingest LongMemEval data into Zep

        Args:
            df: DataFrame containing LongMemEval data
            num_users: Number of users to ingest (default: 500)
        """
        max_users = len(df)
        actual_users = min(num_users, max_users)

        self.logger.info(f"Ingesting {actual_users} users")

        # Create tasks for concurrent processing
        tasks = []
        for user_idx in range(actual_users):
            task = self._process_user(user_idx, df)
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

        self.logger.info("Ingestion completed")
