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
from typing import List, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud import Message
from zep_cloud.client import AsyncZep

from common import DATA_PATH
from zep_ontology import setup_zep_ontology

# Constants for large message handling
MAX_MESSAGE_SIZE = 14000  # Zep memory.add limit
CHUNK_SIZE = 8500  # Target chunk size (allowing room for context)
CHUNK_OVERLAP = 200  # Character overlap between chunks

# Failed multi_session_idx values that need replay (from ingestion_log_20250724_214000.csv analysis)
FAILED_MULTI_SESSION_INDICES = [
    3,
    30,
    53,
    70,
    74,
    86,
    87,
    111,
    126,
    134,
    138,
    169,
    188,
    193,
    195,
    205,
    206,
    224,
    235,
    256,
    285,
    289,
    293,
    295,
    300,
    301,
    322,
    369,
    371,
    386,
    395,
    397,
    398,
    425,
    426,
    461,
    470,
    471,
    487,
    490,
]


class IngestionRunner:
    def __init__(
        self,
        zep_dev_environment: bool = False,
        log_level: str = "INFO",
        use_custom_ontology: bool = False,
        replay_mode: bool = False,
    ):
        load_dotenv()

        self.logger = self._setup_logging(log_level)
        self.use_custom_ontology = use_custom_ontology
        self.replay_mode = replay_mode

        # Initialize Zep client
        if zep_dev_environment:
            self.zep = AsyncZep(
                api_key=os.getenv("ZEP_API_KEY"),
                base_url="https://api.development.getzep.com/api/v2",
            )
        else:
            self.zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

        # Initialize OpenAI client for contextualization
        self.oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Concurrency controls
        self._csv_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(1)  # Limit to 2 concurrent users

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
            prompt = f"""Given this full document and a specific chunk from it, generate a single sentence that:
1. Briefly describes what the overall document is about
2. Explains how this specific chunk relates to or fits within the larger document

Full document (first 2000 chars): {full_document}

Specific chunk: {chunk}

Respond with only a single sentence that provides context for this chunk within the larger document."""

            response = await self.oai_client.chat.completions.create(
                model="gpt-4.1-mini",
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
        self, msg: dict, session_id: str, user_id: str, date_string: str, msg_idx: int
    ) -> dict:
        """Handle large messages via chunking → contextualization → graph.add"""
        result = {
            "original_size": len(msg["content"]),
            "chunks_created": 0,
            "chunks_succeeded": 0,
            "chunking_status": "unknown",
            "chunking_error": "",
        }

        try:
            # Chunk the large message
            chunks = await self._chunk_large_message(msg["content"])
            result["chunks_created"] = len(chunks)
            result["chunking_status"] = "success"

            self.logger.debug(f"Split message {msg_idx} into {len(chunks)} chunks")

            # Process each chunk
            for chunk_idx, chunk in enumerate(chunks):
                try:
                    # Generate context for this chunk
                    context = await self._contextualize_chunk(msg["content"], chunk)

                    # Format as message with context
                    contextualized_content = f"Context: {context} Content: {chunk}"

                    # Create the final graph data
                    graph_data = (
                        f"{msg['role']} ({date_string}): {contextualized_content}"
                    )

                    # Log the contextualized graph data for verification
                    self.logger.info(
                        f"Adding to graph - User: {user_id}, Chunk {chunk_idx + 1}/{len(chunks)}, Size: {len(graph_data)} chars"
                    )
                    self.logger.info(
                        f"Graph data preview: {graph_data[:500]}{'...' if len(graph_data) > 500 else ''}"
                    )

                    # Add to graph using graph.add API
                    await self.zep.graph.add(
                        user_id=user_id,
                        type="text",
                        data=graph_data,
                    )

                    result["chunks_succeeded"] += 1
                    self.logger.debug(
                        f"Successfully added chunk {chunk_idx + 1}/{len(chunks)} for message {msg_idx}"
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
            result["chunking_status"] = "failure"
            result["chunking_error"] = str(e)
            self.logger.error(f"Failed to chunk message {msg_idx}: {e}")

        return result

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
                large_message_results = {}  # Track chunking results for large messages

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

                        # Process messages in batches of 15, handling large messages separately
                        batch_size = 15

                        for batch_start in range(0, len(session), batch_size):
                            batch_end = min(batch_start + batch_size, len(session))
                            batch_messages = []

                            # Create message payloads for this batch
                            for msg_idx in range(batch_start, batch_end):
                                msg = session[msg_idx]

                                # Check if message is too large for memory.add
                                if len(msg["content"]) > MAX_MESSAGE_SIZE:
                                    # Process large message via chunking pipeline
                                    try:
                                        chunk_result = (
                                            await self._process_large_message(
                                                msg,
                                                session_id,
                                                user_id,
                                                date_string.isoformat(),
                                                msg_idx,
                                            )
                                        )
                                        large_message_results[msg_idx] = chunk_result
                                        messages_added += (
                                            1  # Count as one logical message
                                        )
                                        self.logger.info(
                                            f"Processed large message {msg_idx} ({chunk_result['original_size']} chars) "
                                            f"into {chunk_result['chunks_created']} chunks, "
                                            f"{chunk_result['chunks_succeeded']} succeeded"
                                        )
                                    except Exception as large_msg_e:
                                        large_message_results[msg_idx] = {
                                            "original_size": len(msg["content"]),
                                            "chunks_created": 0,
                                            "chunks_succeeded": 0,
                                            "chunking_status": "failure",
                                            "chunking_error": str(large_msg_e),
                                        }
                                        self.logger.error(
                                            f"Failed to process large message {msg_idx}: {large_msg_e}"
                                        )
                                else:
                                    # Normal message processing
                                    message_payload = Message(
                                        role=msg["role"],
                                        role_type=msg["role"],
                                        content=msg["content"],
                                        created_at=date_string.isoformat(),
                                    )
                                    batch_messages.append(message_payload)

                            # Add normal-sized messages to Zep memory if any
                            if batch_messages:
                                try:
                                    await self.zep.memory.add(
                                        session_id=session_id,
                                        messages=batch_messages,
                                    )
                                    messages_added += len(batch_messages)
                                    self.logger.debug(
                                        f"Added batch of {len(batch_messages)} normal messages to session {session_id}"
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

                # Calculate large message processing summary
                large_messages_count = len(large_message_results)
                large_messages_chunks_total = sum(
                    result["chunks_created"]
                    for result in large_message_results.values()
                )
                large_messages_chunks_succeeded = sum(
                    result["chunks_succeeded"]
                    for result in large_message_results.values()
                )
                large_messages_details = (
                    str(large_message_results) if large_message_results else ""
                )

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
                    "large_messages_count": large_messages_count,
                    "large_messages_chunks_total": large_messages_chunks_total,
                    "large_messages_chunks_succeeded": large_messages_chunks_succeeded,
                    "large_messages_details": large_messages_details,
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
        """Ingest conversation data into Zep knowledge graph with detailed logging"""

        # Check if in replay mode - if so, run replay instead of normal ingestion
        if self.replay_mode:
            self.logger.info(
                "Replay mode enabled - replaying failed users instead of normal ingestion"
            )
            await self.replay_failed_users(df)
            return

        # Setup custom ontology if requested
        if self.use_custom_ontology:
            await setup_zep_ontology(self.zep)
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
                "large_messages_count",
                "large_messages_chunks_total",
                "large_messages_chunks_succeeded",
                "large_messages_details",
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

    async def replay_failed_users(self, df: pd.DataFrame) -> None:
        """Replay ingestion for users that failed in previous runs (sequential, no concurrency)"""

        # Setup custom ontology if requested
        if self.use_custom_ontology:
            await setup_zep_ontology(self.zep)

        self.logger.info(
            f"Starting replay for {len(FAILED_MULTI_SESSION_INDICES)} failed users"
        )

        # Set up CSV logging for replay
        log_filename = f"replay_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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
                "large_messages_count",
                "large_messages_chunks_total",
                "large_messages_chunks_succeeded",
                "large_messages_details",
            ]
            writer = csv.DictWriter(log_file, fieldnames=fieldnames)
            writer.writeheader()

            self.logger.info(f"Logging replay details to: {log_filepath}")

            # Process each failed user sequentially with fail-fast behavior
            try:
                for i, multi_session_idx in enumerate(FAILED_MULTI_SESSION_INDICES):
                    user_id = f"lme_s_experiment_user_{multi_session_idx}"

                    self.logger.info(
                        f"Replaying user {i + 1}/{len(FAILED_MULTI_SESSION_INDICES)}: {user_id}"
                    )

                    # Re-ingest the user
                    await self._process_user(
                        multi_session_idx=multi_session_idx,
                        df=df,
                        question_type_filter=None,
                        writer=writer,
                        log_file=log_file,
                    )

                    self.logger.info(f"Completed replay for user {user_id}")

            except Exception as e:
                # Flush CSV and exit on any failure
                log_file.flush()
                self.logger.error(f"Replay failed at user {user_id}: {e}")
                self.logger.info(f"CSV log saved to: {log_filepath}")
                raise

        self.logger.info(f"Replay completed. Detailed log available at: {log_filepath}")
