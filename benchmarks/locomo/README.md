# LOCOMO Evaluation Harness

Modern, production-grade evaluation framework for testing Zep's memory and retrieval capabilities using the LOCOMO dataset.

## Features

- **Unified CLI**: Single entry point for ingestion and evaluation
- **Configuration Management**: YAML-based configuration with Pydantic validation
- **LOCOMO Dataset**: Public benchmark for testing long-term memory
- **Comprehensive Metrics**: Detailed accuracy, latency, and context analysis
- **Timestamped Runs**: All results saved with configuration snapshots for reproducibility
- **Structured Logging**: Configurable logging levels with detailed progress tracking
- **Type Safety**: Full Pydantic models with validation
- **Testing**: Comprehensive test suite with pytest
- **Modern Tooling**: Ruff, pyright, and development automation with Makefile

## Architecture

### Module Structure

```
locomo/
├── benchmark.py           # Main CLI entry point
├── config.py             # Configuration management (Pydantic)
├── benchmark_config.yaml # YAML configuration file
├── common.py             # Shared data models
├── constants.py          # Constants and paths
├── prompts.py            # Prompt templates
├── ingestion.py          # Data ingestion module
├── evaluation.py         # Evaluation pipeline
├── persistence.py        # Results storage
├── tests/                # Test suite
│   ├── test_config.py
│   ├── test_common.py
│   └── test_persistence.py
├── Makefile              # Development automation
├── pyproject.toml        # Modern Python packaging
└── README.md
```

### Key Design Patterns

- **Factory Pattern**: Configuration loading and client creation
- **Repository Pattern**: Results persistence with timestamped runs
- **Builder Pattern**: Pydantic-based configuration
- **Semaphore Pattern**: Concurrency control for rate limiting

## Installation

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Zep API key
- OpenAI API key

### Setup

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
make install

# Or manually
uv sync
```

### Environment Variables

Create a `.env` file:

```bash
ZEP_API_KEY=your_zep_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

## Configuration

Edit `benchmark_config.yaml` to customize evaluation parameters:

```yaml
# Concurrency limits
evaluation_concurrency: 10
ingestion_concurrency: 5

# Graph search parameters
graph_params:
  edge_limit: 20
  edge_reranker: "cross_encoder"
  node_limit: 20
  node_reranker: "rrf"

# Model configuration
models:
  response_model: "gpt-4o-mini"
  response_temperature: 0.0
  grader_model: "gpt-4o-mini"
  grader_temperature: 0.0

# LOCOMO-specific settings
locomo:
  num_users: 10
  max_session_count: 35
  data_url: "https://raw.githubusercontent.com/snap-research/locomo/refs/heads/main/data/locomo10.json"
```

### Configuration Options

#### Graph Parameters
- `edge_limit`: Number of facts to retrieve (1-100)
- `edge_reranker`: Reranking method for facts (`cross_encoder`, `rrf`, `mmr`)
- `node_limit`: Number of entities to retrieve (1-100)
- `node_reranker`: Reranking method for entities

#### Model Configuration
- `response_model`: OpenAI model for response generation
- `response_temperature`: Temperature for response generation (0.0-2.0)
- `grader_model`: OpenAI model for grading
- `grader_temperature`: Temperature for grading

#### LOCOMO Settings
- `num_users`: Number of users to evaluate (1-10 for LOCOMO10 dataset)
- `max_session_count`: Maximum sessions per user
- `data_url`: URL to download LOCOMO dataset from

## Usage

```bash
# Ingest LOCOMO data (downloads and ingests into Zep)
python benchmark.py --ingest

# Run evaluation
python benchmark.py --eval

# With debug logging
python benchmark.py --eval --log-level DEBUG

# With custom config
python benchmark.py --eval --config benchmark_config.yaml
```

### Makefile Commands

```bash
make help       # Show available commands
make install    # Install dependencies
make test       # Run tests with coverage
make format     # Format code
make lint       # Lint code
make check      # Run all checks (format + lint + test)
make clean      # Clean generated files
make ingest     # Run ingestion
make eval       # Run evaluation
```

## Results

### Output Structure

Each evaluation run creates a timestamped directory:

```
experiments/
└── run_20251121_153000/
    ├── results.json    # Complete results with metrics
    └── config.yaml     # Configuration snapshot
```

### Results Format

`results.json` contains:

```json
{
  "run_id": "run_20251121_153000",
  "timestamp": "20251121_153000",
  "dataset": "locomo",
  "metrics": {
    "accuracy": 0.857,
    "correct_count": 6,
    "total_count": 7,
    "retrieval_duration_stats": {
      "median": 0.45,
      "p95": 0.89,
      "p99": 1.2
    },
    "by_category": [...],
    "by_difficulty": [...]
  },
  "results": [...]
}
```

### Metrics Included

- **Overall Accuracy**: Percentage of correct responses
- **Latency Statistics**: Retrieval, response, and total duration (median, p90, p95, p99)
- **Context Analysis**: Token and character counts (median, mean, p95, p99)
- **Category Breakdown**: Accuracy per test category (navigation, media, communication)
- **Difficulty Breakdown**: Accuracy per difficulty level (easy, medium, hard)

## Dataset

### LOCOMO Dataset

Public benchmark from [SNAP Research](https://github.com/snap-research/locomo):
- 10 synthetic users
- ~35 conversation sessions per user
- Multi-turn conversations with temporal reasoning
- Tests long-term memory and cross-session recall
- Categories: navigation, media playback, communication
- Difficulty levels: easy, medium, hard

## Development

### Running Tests

```bash
# Run all tests
make test

# Run specific test file
uv run pytest tests/test_config.py -v

# Run with coverage
uv run pytest --cov=. --cov-report=term-missing
```

### Code Quality

```bash
# Format code
make format

# Lint code
make lint

# Run all checks
make check
```

### Adding New Datasets

To adapt the harness for a new dataset:

1. Add dataset-specific config to `config.py`:
   ```python
   class NewDatasetConfig(BaseModel):
       # ... config fields
   ```

2. Implement ingestion in `ingestion.py`:
   ```python
   async def ingest_newdataset(self) -> ...:
       # ... ingestion logic
   ```

3. Implement evaluation in `evaluation.py`:
   ```python
   async def evaluate_newdataset(self, ...) -> list[EvaluationResult]:
       # ... evaluation logic
   ```

4. Update prompts in `prompts.py` if needed:
   ```python
   RESPONSE_PROMPT = """..."""  # Customize for your dataset
   GRADER_PROMPT = """..."""
   ```

5. Update CLI in `benchmark.py` to call your new methods

## Troubleshooting

### Common Issues

**"LOCOMO data not found"**
- Run `python benchmark.py --ingest` first to download and ingest data

**"Zep API key not found"**
- Create `.env` file with `ZEP_API_KEY=your_key_here`
- Or set environment variable: `export ZEP_API_KEY=your_key_here`

**Rate limiting errors**
- Reduce `evaluation_concurrency` in config
- Reduce `ingestion_concurrency` in config

### Debug Mode

Enable debug logging to see detailed execution:

```bash
python benchmark.py --eval --log-level DEBUG
```

## Design Decisions

### Why Pydantic?
- Type safety with runtime validation
- Excellent IDE support and autocomplete
- Serialization to/from JSON and YAML
- Clear error messages for invalid configurations

### Why Timestamped Runs?
- Enables comparing different configurations
- Configuration snapshots ensure reproducibility
- No accidental result overwrites
- Easy to track experiment progression

### Why Separate Ingestion and Evaluation?
- Ingestion is slow and only needs to run once
- Enables multiple evaluations with different parameters
- Faster iteration during experimentation
- Clear separation of concerns

### Why LOCOMO?
- Public benchmark for standardized comparisons
- Tests long-term memory and cross-session recall
- Temporal reasoning capabilities
- Multi-turn conversational understanding

## Performance

### Expected Latency

- **Retrieval**: 200-800ms (depends on graph size)
- **Response Generation**: 500-2000ms (depends on model and context)
- **Total per Test**: 1-3 seconds

### Concurrency Guidelines

- **Evaluation**: 10-20 parallel tests (default: 10)
- **Ingestion**: 5-10 parallel users (default: 5)
- Higher values may hit rate limits

## License

See main repository license.

## Contributing

See implementation modules for details:
- Context composition: `evaluation.py` (`_compose_context`)
- Response generation: `evaluation.py` (`_generate_response`)
- Grading logic: `evaluation.py` (`_grade_response`)
- Prompt templates: `prompts.py`
