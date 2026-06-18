# Makefile for zep-ag2 development

.PHONY: help install format lint type-check test test-cov clean build all

# Default target
help:
	@echo "Available commands:"
	@echo "  install     - Install package and dependencies in development mode"
	@echo "  format      - Format code with ruff"
	@echo "  lint        - Run linting checks"
	@echo "  type-check  - Run type checking with mypy"
	@echo "  test        - Run tests"
	@echo "  test-cov    - Run tests with coverage report"
	@echo "  all         - Run format, lint, type-check, and test"
	@echo "  build       - Build the package"
	@echo "  clean       - Clean build artifacts"

# Install package in development mode
install:
	uv sync --extra dev
	# Workaround: macOS sets UF_HIDDEN on all files inside .venv (dot-directory),
	# causing Python 3.11 to skip all .pth files. We mirror src/zep_ag2 directly
	# into site-packages so the package is importable without .pth.
	@SITE=$$(ls -d .venv/lib/python*/site-packages 2>/dev/null | head -1); \
	if [ -n "$$SITE" ]; then \
	  rm -rf "$$SITE/zep_ag2"; \
	  cp -r src/zep_ag2 "$$SITE/zep_ag2"; \
	  echo "Installed zep_ag2 into $$SITE"; \
	fi

# Format code
format:
	uv run ruff format .

# Run linting checks
lint:
	uv run ruff check .

# Fix linting issues automatically
lint-fix:
	uv run ruff check --fix .

# Run type checking
type-check:
	uv run mypy src/

# Run tests
test:
	uv run pytest tests/ -v

# Run tests with coverage
test-cov:
	uv run pytest tests/ -v --cov=zep_ag2 --cov-report=term-missing --cov-report=xml

# Run all checks (the order matters: format first, then lint, then type-check, then test)
all: format lint type-check test

# Build the package
build:
	uv build

# Clean build artifacts
clean:
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -f coverage.xml
	rm -f .coverage

# Development workflow - run this before committing
pre-commit: lint-fix format lint type-check test
	@echo "All checks passed! Ready to commit."

# CI workflow - strict checks without auto-fixing
ci: lint type-check test
	@echo "CI checks passed!"
