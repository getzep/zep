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

## Configuration

Edit `benchmark_config.yaml`:

```yaml
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

**Ingest data:**
```bash
uv run python benchmark.py --ingest --num-users 500
```

**Run evaluation:**
```bash
uv run python benchmark.py --eval --num-users 500
```

**Options:**
- `--num-users N`: Number of users to process (default: 500)
- `--log-level LEVEL`: DEBUG, INFO, WARNING, ERROR, CRITICAL
- `--skip-download`: Skip dataset download (ingest only)
- `--experiments-dir DIR`: Results directory (eval only, default: experiments)

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

## Testing

```bash
# Run tests
uv run pytest

# With coverage
uv run pytest --cov=. --cov-report=term-missing

# Type checking
uv run pyright .

# Linting
uv run ruff check .
```

## Troubleshooting

**Dataset download fails:** Check internet connection and disk space

**API rate limits:** Reduce number of users or upgrade Zep plan

**Authentication errors:** Verify environment variables are set

**Debug logging:**
```bash
uv run python benchmark.py --eval --log-level DEBUG
```

## Tips

- Start with `--num-users 50` to verify setup
- Monitor API usage during large runs
- Reasoning models (GPT-5, o1, o3) have higher latency
