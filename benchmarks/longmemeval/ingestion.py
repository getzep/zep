#!/usr/bin/env python3
"""
Data ingestion module for LongMemEval benchmark
"""

import asyncio
import csv
import gdown
import logging
import os
import pandas as pd
import tarfile
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from zep_cloud import Message
from zep_cloud.client import AsyncZep

from common import DATA_PATH
from zep_ontology import setup_zep_ontology


class IngestionRunner:
    def __init__(
        self,
        zep_dev_environment: bool = False,
        log_level: str = "INFO",
        use_custom_ontology: bool = False,
    ):
        load_dotenv()

        self.logger = self._setup_logging(log_level)
        self.use_custom_ontology = use_custom_ontology

        # Initialize Zep client
        if zep_dev_environment:
            self.zep = AsyncZep(
                api_key=os.getenv("ZEP_API_KEY"),
                base_url="https://api.development.getzep.com/api/v2",
            )
        else:
            self.zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

        # Concurrency controls
        self._csv_lock = asyncio.Lock()
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
        multi_session_idx: int,
        df: pd.DataFrame,
        question_type_filter: Optional[str],
        writer: csv.DictWriter,
        log_file,
    ):
        """Process a single user's data with all their sessions"""
        async with self._semaphore:  # Limit concurrent users
            # Get session data
            multi_session = df["haystack_sessions"].iloc[multi_session_idx]
            multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]
            question_type = df["question_type"][multi_session_idx]

            # Apply question type filter
            if question_type_filter and question_type != question_type_filter:
                return

            self.logger.info(f"Processing session {multi_session_idx}: {question_type}")

            # Initialize user-level tracking
            user_id = f"lme_s_experiment_user_{multi_session_idx}"
            user_creation_status = "unknown"
            user_error = ""

            # Attempt user creation
            try:
                await self.zep.user.add(user_id=user_id)
                user_creation_status = "success"
                self.logger.debug(f"User {user_id} created successfully")
            except Exception as e:
                user_creation_status = "failure"
                user_error = str(e)
                self.logger.error(f"Failed to create user {user_id}: {e}")

            # Process each session for this user, even if user creation failed
            # (in case user already exists)
            for session_idx, session in enumerate(multi_session):
                session_id = (
                    f"lme_s_experiment_session_{multi_session_idx}_{session_idx}"
                )
                session_creation_status = "unknown"
                session_error = ""
                messages_attempted = len(session)
                messages_added = 0
                message_addition_status = "unknown"
                message_error = ""

                # Attempt session creation
                try:
                    await self.zep.memory.add_session(
                        user_id=user_id, session_id=session_id
                    )
                    session_creation_status = "success"
                    self.logger.debug(f"Session {session_id} created successfully")
                except Exception as e:
                    session_creation_status = "failure"
                    session_error = str(e)
                    self.logger.error(f"Failed to create session {session_id}: {e}")

                # Attempt to add messages to session
                if session_creation_status == "success" and messages_attempted > 0:
                    try:
                        # Validate data structure consistency
                        if session_idx >= len(multi_session_dates):
                            raise IndexError(
                                f"session_idx {session_idx} exceeds multi_session_dates length {len(multi_session_dates)}"
                            )

                        # Parse and format timestamp (same for all messages in this session)
                        date = multi_session_dates[session_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=timezone.utc
                        )

                        # Process messages in batches of 15
                        batch_size = 15
                        for batch_start in range(0, len(session), batch_size):
                            batch_end = min(batch_start + batch_size, len(session))
                            batch_messages = []

                            # Create message payloads for this batch
                            for msg_idx in range(batch_start, batch_end):
                                msg = session[msg_idx]
                                message_payload = Message(
                                    role=msg["role"],
                                    role_type=msg["role"],
                                    content=msg["content"],
                                    created_at=date_string.isoformat(),
                                )
                                batch_messages.append(message_payload)

                            try:
                                # Add batch to Zep
                                await self.zep.memory.add(
                                    session_id=session_id,
                                    messages=batch_messages,
                                )
                                messages_added += len(batch_messages)
                                self.logger.debug(
                                    f"Added batch of {len(batch_messages)} messages to session {session_id}"
                                )

                            except Exception as batch_e:
                                message_error = f"Batch {batch_start}-{batch_end - 1} failed: {str(batch_e)}"
                                self.logger.warning(
                                    f"Failed to add batch {batch_start}-{batch_end - 1} to session {session_id}: {batch_e}"
                                )
                                break  # Stop processing remaining batches for this session

                        # Determine overall message addition status
                        if messages_added == messages_attempted:
                            message_addition_status = "success"
                        elif messages_added > 0:
                            message_addition_status = "partial"
                        else:
                            message_addition_status = "failure"
                            if not message_error:
                                message_error = "No messages were added successfully"

                    except Exception as e:
                        message_addition_status = "failure"
                        message_error = str(e)
                        self.logger.error(
                            f"Failed to process messages for session {session_id}: {e}"
                        )

                elif messages_attempted == 0:
                    message_addition_status = "empty_session"
                    message_error = "Session has no messages"
                    self.logger.warning(f"Session {session_id} has no messages to add")

                else:
                    message_addition_status = "skipped"
                    message_error = "Session creation failed"

                # Log this session's results with concurrency safety
                log_entry = {
                    "multi_session_idx": multi_session_idx,
                    "user_id": user_id,
                    "user_creation_status": user_creation_status,
                    "user_error": user_error,
                    "session_idx": session_idx,
                    "session_id": session_id,
                    "session_creation_status": session_creation_status,
                    "session_error": session_error,
                    "messages_attempted": messages_attempted,
                    "messages_added": messages_added,
                    "message_addition_status": message_addition_status,
                    "message_error": message_error,
                    "question_type": question_type,
                }

                # Thread-safe CSV writing
                async with self._csv_lock:
                    writer.writerow(log_entry)
                    log_file.flush()  # Ensure data is written immediately

                # Log summary for this session
                status_summary = f"Session {session_id}: {session_creation_status}"
                if session_creation_status == "success":
                    status_summary += f", messages: {messages_added}/{messages_attempted} ({message_addition_status})"
                self.logger.info(status_summary)

            # Log summary for this multi-session
            self.logger.info(
                f"Completed processing multi-session {multi_session_idx} with {len(multi_session)} sessions"
            )

    async def download_dataset(
        self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")
    ):
        """Download and extract the LongMemEval dataset from Google Drive"""
        file_id = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"
        url = f"https://drive.google.com/uc?id={file_id}"

        # Create data directory
        if not os.path.exists(DATA_PATH):
            os.makedirs(DATA_PATH)

        # Download if needed
        if not os.path.exists(file_path):
            self.logger.info(f"Downloading dataset to {file_path}...")
            gdown.download(url, file_path, quiet=False)
        else:
            self.logger.info(f"'{file_path}' already exists, skipping download.")

        # Extract if needed
        oracle_path = os.path.join(DATA_PATH, "longmemeval_oracle.json")
        if not os.path.exists(oracle_path):
            self.logger.info("Extracting dataset...")
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=DATA_PATH, filter="data")
        else:
            self.logger.info(
                "'longmemeval_oracle.json' already exists, skipping extraction."
            )

    async def ingest_data(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        question_type_filter: Optional[str] = None,
        start_index: int = 0,
    ):
        # Setup custom ontology if requested
        if self.use_custom_ontology:
            await setup_zep_ontology(self.zep)

        """Ingest conversation data into Zep knowledge graph with detailed logging"""
        filter_msg = (
            f"question type: {question_type_filter}"
            if question_type_filter
            else "all question types"
        )
        # Ensure we don't exceed dataset bounds
        max_sessions = len(df)
        end_index = min(start_index + num_sessions, max_sessions)
        actual_sessions = end_index - start_index

        self.logger.info(
            f"Ingesting {actual_sessions} sessions (indices {start_index}-{end_index - 1}) with {filter_msg}"
        )

        # Set up CSV logging
        log_filename = f"ingestion_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        log_filepath = os.path.join(DATA_PATH, log_filename)

        # Ensure data directory exists
        if not os.path.exists(DATA_PATH):
            os.makedirs(DATA_PATH)

        with open(log_filepath, "w", newline="", encoding="utf-8") as log_file:
            fieldnames = [
                "multi_session_idx",
                "user_id",
                "user_creation_status",
                "user_error",
                "session_idx",
                "session_id",
                "session_creation_status",
                "session_error",
                "messages_attempted",
                "messages_added",
                "message_addition_status",
                "message_error",
                "question_type",
            ]
            writer = csv.DictWriter(log_file, fieldnames=fieldnames)
            writer.writeheader()

            self.logger.info(f"Logging ingestion details to: {log_filepath}")

            # Create tasks for concurrent user processing (5 at a time)
            self.logger.info(
                "Starting concurrent processing with up to 5 users at a time"
            )

            tasks = []
            for multi_session_idx in range(start_index, end_index):
                task = self._process_user(
                    multi_session_idx=multi_session_idx,
                    df=df,
                    question_type_filter=question_type_filter,
                    writer=writer,
                    log_file=log_file,
                )
                tasks.append(task)

            # Execute all user processing tasks concurrently
            await asyncio.gather(*tasks)

        self.logger.info(
            f"Ingestion completed. Detailed log available at: {log_filepath}"
        )
