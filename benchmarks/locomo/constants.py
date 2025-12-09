"""Constants for LOCOMO evaluation harness."""

# Concurrency limits
DEFAULT_EVALUATION_CONCURRENCY = 10
DEFAULT_INGESTION_CONCURRENCY = 5

# File paths
DATA_DIR = "data"
EXPERIMENTS_DIR = "experiments"
CACHE_DIR = ".cache"

# Timeouts (seconds)
DEFAULT_TIMEOUT = 120

# Token limits
MAX_CONTEXT_TOKENS = 100000

# Data source
LOCOMO_DATA_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/refs/heads/main/data/locomo10.json"
)
