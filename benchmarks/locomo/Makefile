.PHONY: help install test format lint check clean ingest eval

help:
	@echo "LOCOMO Evaluation Harness - Available commands:"
	@echo ""
	@echo "  make install    Install dependencies using uv"
	@echo "  make test       Run tests with coverage"
	@echo "  make format     Format code with ruff"
	@echo "  make lint       Lint code with ruff and pyright"
	@echo "  make check      Run format, lint, and test"
	@echo "  make clean      Clean generated files"
	@echo "  make ingest     Ingest data (LOCOMO or Rivian)"
	@echo "  make eval       Run evaluation"
	@echo ""

install:
	@echo "Installing dependencies..."
	uv sync

test:
	@echo "Running tests..."
	uv run pytest --cov=. --cov-report=term-missing --cov-report=html

format:
	@echo "Formatting code..."
	uv run ruff format .
	uv run ruff check --fix .

lint:
	@echo "Linting code..."
	uv run ruff check .
	uv run pyright .

check: format lint test
	@echo "All checks passed!"

clean:
	@echo "Cleaning generated files..."
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf .ruff_cache
	rm -rf __pycache__
	rm -rf **/__pycache__
	rm -rf *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

ingest:
	@echo "Running ingestion..."
	uv run python benchmark.py --ingest

eval:
	@echo "Running evaluation..."
	uv run python benchmark.py --eval
