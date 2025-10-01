.PHONY: help test format lint check clean

help:
	@echo "Available targets:"
	@echo "  make test    - Run all tests with coverage"
	@echo "  make format  - Format code with ruff"
	@echo "  make lint    - Run linters (ruff and pyright)"
	@echo "  make check   - Run format, lint, and test (all checks)"
	@echo "  make clean   - Remove generated files and caches"

test:
	uv run pytest --cov=. --cov-report=term-missing

format:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .
	uv run pyright .

check: format lint test
	@echo "✓ All checks passed!"

clean:
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
