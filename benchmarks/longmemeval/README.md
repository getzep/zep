# LongMemEval Benchmark for Zep

Evaluates Zep's long-term memory capabilities across multi-thread conversations.

## Prerequisites

**Required API Keys:**
```bash
export ZEP_API_KEY="your_zep_api_key"
export OPENAI_API_KEY="your_openai_api_key"
```

**Install Dependencies:**
```bash
# Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

## How It Works

### Progressive Ingestion with Checkpointing

The benchmark supports incremental data ingestion with automatic checkpointing:

1. **User-level processing**: Each user's data (including all their threads) is processed as an atomic unit
2. **Checkpoint after each user**: Progress is saved to `data/ingestion_checkpoint.json` after completing each user
3. **Automatic cleanup on failure**: If any thread fails for a user, the entire user is deleted from Zep and marked as failed
4. **Resume from checkpoint**: Use `--continue` to skip already-processed users and resume where you left off
5. **Incremental limits**: Use `--num-users` to control how many users to process in each run

This design allows you to:
- Ingest large datasets in manageable batches
- Recover from interruptions without data loss
- Avoid rate limits by spreading ingestion across multiple runs
- Maintain data consistency (all threads for a user succeed or all fail)

## Configuration

Edit `benchmark_config.yaml`:

```yaml
concurrency: 2  # Max concurrent users during ingestion (1-10)

graph_params:
  edge_limit: [20]        # Number of fact edges to retrieve
  node_limit: [5]         # Number of entity nodes to retrieve
  episode_limit: [5]      # Number of conversation episodes (0 = disabled)
  edge_reranker: [cross_encoder]
  node_reranker: [cross_encoder]
  episode_reranker: [cross_encoder]

models:
  response_model: [gpt-4.1]
  grader_model: [gpt-4.1]
  temperature: [0.0]
```

**For reasoning models (GPT-5, o1, o3):**
```yaml
models:
  response_model: [gpt-5]
  grader_model: [gpt-4.1]
  reasoning_effort: [high]              # minimal, low, medium, or high
  max_completion_tokens: [10000]
  # Do not use temperature or max_tokens with reasoning models
```

## Usage

### Ingestion

**Ingest all data:**
```bash
uv run python benchmark.py --ingest --num-users 500
```

**Progressive ingestion with checkpointing:**
```bash
# Ingest first 50 users (0-49)
uv run python benchmark.py --ingest --num-users 50

# Continue with next 50 users (50-99)
uv run python benchmark.py --ingest --num-users 50 --continue

# Continue with next 50 users (100-149)
uv run python benchmark.py --ingest --num-users 50 --continue

# Or jump to completion (process remaining up to 500)
uv run python benchmark.py --ingest --num-users 500 --continue
```

**Options:**
- `--num-users N`: Number of users to process
  - Without `--continue`: Ingest users 0 to N-1 (default: 500)
  - With `--continue`: Ingest N additional users beyond checkpoint
- `--continue`: Continue from previous checkpoint
- `--skip-download`: Skip dataset download
- `--log-level LEVEL`: DEBUG, INFO, WARNING, ERROR, CRITICAL

**Checkpoint Behavior:**
- Checkpoint saved to [data/ingestion_checkpoint.json](data/ingestion_checkpoint.json) after each user
- Tracks completed and failed users
- `--continue` resumes from checkpoint, skipping already processed users
- Fresh runs (without `--continue`) reset the checkpoint
- If any thread fails, the entire user is deleted from Zep and marked as failed

### Evaluation

**Run evaluation:**
```bash
uv run python benchmark.py --eval --num-users 500
```

**Options:**
- `--num-users N`: Number of users to evaluate (default: 500)
- `--experiments-dir DIR`: Results directory (default: experiments)
- `--log-level LEVEL`: DEBUG, INFO, WARNING, ERROR, CRITICAL

## Results

Results are saved to `experiments/run_TIMESTAMP/`:

```
experiments/
└── run_20250930_143022/
    ├── results.json
    └── config.yaml
```

Example metrics:

```json
{
  "metrics": {
    "accuracy": 0.756,
    "correct_count": 378,
    "total_count": 500,
    "avg_response_duration": 2.341,
    "avg_retrieval_duration": 0.892
  }
}
```

## Warnings

### ⚠️ Zep Metered Billing Not Supported

**The benchmark cannot run on Zep's Metered Billing tier** due to message size limits and rate limiting. **A paid Zep plan is required.**

### ⚠️ Terms of Service

Customer benchmarking for internal evaluation is permitted. **Publicly publishing benchmark results or evaluation data violates Zep's Terms of Service.**

Contact: founders@getzep.com

## Development

### Makefile Commands

```bash
make test     # Run tests with coverage
make format   # Format code with ruff
make lint     # Run linters (ruff + pyright)
make check    # Run all checks (format + lint + test)
make clean    # Remove generated files
```

### Manual Commands

```bash
# Run tests
uv run pytest

# With coverage
uv run pytest --cov=. --cov-report=term-missing

# Type checking
uv run pyright .

# Linting
uv run ruff check .

# Format code
uv run ruff format .
```

## Troubleshooting

**Dataset download fails:** Check internet connection and disk space

**API rate limits:** Ingest in smaller batches using `--num-users` with `--continue`

**Authentication errors:** Verify environment variables are set

**Ingestion interrupted:** Use `--continue` to resume from checkpoint

**Reset checkpoint:** Delete [data/ingestion_checkpoint.json](data/ingestion_checkpoint.json) and run without `--continue`

**Failed user ingestion:** Users are automatically deleted from Zep if any thread fails. Check logs for details.

**Debug logging:**
```bash
uv run python benchmark.py --ingest --log-level DEBUG
uv run python benchmark.py --eval --log-level DEBUG
```

## Tips

- Start with `--num-users 50` to verify setup
- Monitor API usage during large runs
- Reasoning models (GPT-5, o1, o3) have higher latency
