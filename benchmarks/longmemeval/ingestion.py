#!/usr/bin/env python3
"""
Data ingestion module for LongMemEval benchmark
"""

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
    def __init__(self, zep_dev_environment: bool = False, log_level: str = "INFO", use_custom_ontology: bool = False):
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
        
        # Setup custom ontology if requested
        if self.use_custom_ontology:
            self._setup_ontology()

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

    def _setup_ontology(self):
        """Setup custom Zep ontology for improved knowledge graph structure"""
        try:
            # Note: setup_zep_ontology expects a synchronous client
            # We'll need to create a sync client for ontology setup
            from zep_cloud.client import Zep
            
            # Create synchronous client for ontology setup
            if hasattr(self.zep, 'base_url') and 'development' in str(self.zep.base_url):
                sync_client = Zep(
                    api_key=os.getenv("ZEP_API_KEY"),
                    base_url="https://api.development.getzep.com/api/v2",
                )
            else:
                sync_client = Zep(api_key=os.getenv("ZEP_API_KEY"))
            
            setup_zep_ontology(sync_client)
            self.logger.info("✅ Custom ontology configured successfully for improved knowledge graph structure")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to configure custom ontology: {e}")
            self.logger.warning("Continuing with default Zep ontology...")

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
        
        with open(log_filepath, 'w', newline='', encoding='utf-8') as log_file:
            fieldnames = ['multi_session_idx', 'user_id', 'user_creation_status', 'user_error', 
                         'session_idx', 'session_id', 'session_creation_status', 'session_error',
                         'messages_attempted', 'messages_added', 'message_addition_status', 
                         'message_error', 'question_type']
            writer = csv.DictWriter(log_file, fieldnames=fieldnames)
            writer.writeheader()
            
            self.logger.info(f"Logging ingestion details to: {log_filepath}")

            for multi_session_idx in range(start_index, end_index):
                # Get session data
                multi_session = df["haystack_sessions"].iloc[multi_session_idx]
                multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]
                question_type = df["question_type"][multi_session_idx]

                # Apply question type filter
                if question_type_filter and question_type != question_type_filter:
                    continue

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
                    session_id = f"lme_s_experiment_session_{multi_session_idx}_{session_idx}"
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
                                raise IndexError(f"session_idx {session_idx} exceeds multi_session_dates length {len(multi_session_dates)}")
                            
                            # Add messages to session
                            for msg_idx, msg in enumerate(session):
                                try:
                                    # Parse and format timestamp
                                    date = multi_session_dates[session_idx] + " UTC"
                                    date_format = "%Y/%m/%d (%a) %H:%M UTC"
                                    date_string = datetime.strptime(date, date_format).replace(
                                        tzinfo=timezone.utc
                                    )

                                    # Create message payload
                                    message_payload = Message(
                                        role=msg["role"],
                                        role_type=msg["role"],
                                        content=msg["content"],
                                        created_at=date_string.isoformat(),
                                    )

                                    # Add to Zep
                                    await self.zep.memory.add(
                                        session_id=session_id,
                                        messages=[message_payload],
                                    )
                                    messages_added += 1
                                    
                                except Exception as msg_e:
                                    message_error = f"Message {msg_idx} failed: {str(msg_e)}"
                                    self.logger.warning(f"Failed to add message {msg_idx} to session {session_id}: {msg_e}")
                                    break  # Stop processing remaining messages for this session
                            
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
                            self.logger.error(f"Failed to process messages for session {session_id}: {e}")
                    
                    elif messages_attempted == 0:
                        message_addition_status = "empty_session"
                        message_error = "Session has no messages"
                        self.logger.warning(f"Session {session_id} has no messages to add")
                    
                    else:
                        message_addition_status = "skipped"
                        message_error = "Session creation failed"
                    
                    # Log this session's results
                    log_entry = {
                        'multi_session_idx': multi_session_idx,
                        'user_id': user_id,
                        'user_creation_status': user_creation_status,
                        'user_error': user_error,
                        'session_idx': session_idx,
                        'session_id': session_id,
                        'session_creation_status': session_creation_status,
                        'session_error': session_error,
                        'messages_attempted': messages_attempted,
                        'messages_added': messages_added,
                        'message_addition_status': message_addition_status,
                        'message_error': message_error,
                        'question_type': question_type
                    }
                    
                    writer.writerow(log_entry)
                    log_file.flush()  # Ensure data is written immediately
                    
                    # Log summary for this session
                    status_summary = f"Session {session_id}: {session_creation_status}"
                    if session_creation_status == "success":
                        status_summary += f", messages: {messages_added}/{messages_attempted} ({message_addition_status})"
                    self.logger.info(status_summary)
                
                # Log summary for this multi-session
                self.logger.info(f"Completed processing multi-session {multi_session_idx} with {len(multi_session)} sessions")

        self.logger.info(f"Ingestion completed. Detailed log available at: {log_filepath}")