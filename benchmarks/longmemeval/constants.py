#!/usr/bin/env python3
"""
Constants for LongMemEval benchmark
"""

# Concurrency
DEFAULT_CONCURRENCY = 2

# Models
RESPONSE_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"
CONTEXTUALIZATION_MODEL = "gpt-4.1-mini"

# Message size limits
MAX_MESSAGE_SIZE = 14000  # Max characters for thread.add_messages
CHUNK_SIZE = 9000  # Target chunk size for graph.add (max 10000)
CHUNK_OVERLAP = 200  # Overlap between chunks for context
MAX_BATCH_SIZE = 30  # Max messages per thread.add_messages batch

# Paths
DATA_PATH = "data"
CHECKPOINT_FILE = "data/ingestion_checkpoint.json"
