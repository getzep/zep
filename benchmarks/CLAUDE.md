# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains research experiments and publications related to Zep's temporal knowledge graph architecture for agent memory. The main research paper demonstrates state-of-the-art agent memory capabilities through two evaluation frameworks: LOCOMO and LongMemEval.

## Key Components

### Evaluation Frameworks
- **LOCOMO evaluation**: Tests agent memory on conversational QA tasks with temporal reasoning
- **LongMemEval**: Evaluates long-term memory capabilities across multiple sessions
- **DMR experiment**: Based on the MemGPT paper methodology

### Core Architecture
- **Zep Integration**: Uses `zep-cloud` client for knowledge graph operations
- **OpenAI Integration**: Leverages GPT models for evaluation and grading
- **Async Processing**: All evaluation scripts use asyncio for concurrent processing

## Development Environment

### Dependencies
This project uses uv for package management with workspace support:
```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all workspace dependencies
uv sync --python 3.12

# Install specific workspace dependencies
uv sync --package locomo-eval --python 3.12
uv sync --package longmemeval --python 3.12
```

**Requirements:**
- Python 3.12 or higher

### Environment Variables
Required API keys:
- `ZEP_API_KEY`: For Zep cloud services
- `OPENAI_API_KEY`: For OpenAI API access

### Running Experiments

#### LOCOMO Evaluation
```bash
# Using uv run for workspace execution
uv run --package locomo-eval python zep_locomo_ingestion.py  # Ingest data first
uv run --package locomo-eval python zep_locomo_eval.py       # Run evaluation

# Or cd to directory and use uv run
cd locomo_eval
uv run python zep_locomo_ingestion.py
uv run python zep_locomo_eval.py
```

#### LongMemEval
```bash
# Run Jupyter notebook
cd longmemeval
uv run jupyter notebook zep_longmem_eval.ipynb

# Or run standalone script
uv run --package longmemeval python zep_longmem_eval.py
cd longmemeval && uv run python zep_longmem_eval.py
```

## Code Structure

### Data Flow
1. **Ingestion Scripts**: Load conversational data into Zep knowledge graph
2. **Evaluation Scripts**: Query Zep for relevant context and test responses
3. **Grading Functions**: Use LLM-based grading to score responses against gold standards

### Key Files
- `locomo_eval/zep_locomo_eval.py`: Main LOCOMO evaluation logic
- `locomo_eval/zep_locomo_ingestion.py`: Ingests LOCOMO dataset into Zep
- `locomo_eval/zep_locomo_search.py`: Search functionality for LOCOMO
- `longmemeval/zep_longmem_eval.ipynb`: LongMemEval notebook with baseline comparisons  
- `longmemeval/zep_longmem_eval.py`: Standalone LongMemEval script
- `longmemeval/Zep Test Harness/zep_eval.py`: Core evaluation utilities and grading functions

### Evaluation Patterns
- All evaluations use async/await for concurrent processing
- Grading uses structured output with Pydantic models
- Results are saved as JSON/JSONL files for analysis
- Baseline comparisons provide context-aware evaluation

## Model Configuration
- Default model: `gpt-4.1-mini` (configurable in code)
- Grading uses structured output parsing with Pydantic models
- Temperature set to 0 for deterministic evaluation
- Uses OpenAI's beta.chat.completions.parse for structured outputs

## Common Development Tasks

### Running Individual Components
- **Search only**: `uv run --package locomo-eval python zep_locomo_search.py`
- **Response generation**: `uv run --package locomo-eval python zep_locomo_responses.py`
- **Evaluation with existing data**: Skip ingestion and run evaluation scripts directly

### Package Management
- **Install all dependencies**: `uv sync`
- **Install specific workspace**: `uv sync --package locomo-eval` or `uv sync --package longmemeval`
- **Add new dependency**: `uv add <package>` (to root) or `uv add --package <workspace> <package>`
- **Run commands in workspace**: `uv run --package <workspace> <command>`

### Data Management
- Results are saved as JSON/JSONL files in respective data/ directories
- LOCOMO results stored in `locomo_eval/data/`
- LongMemEval results stored in notebooks and can be exported to JSON

### Debugging
- All scripts use asyncio - ensure proper async/await patterns
- Check API keys are set in environment variables
- Verify Zep base URL configuration (defaults to development environment)