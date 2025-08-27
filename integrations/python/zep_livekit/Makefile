# Zep LiveKit Integration Development Makefile

.PHONY: help install format lint type-check test all pre-commit ci clean

# Default target
help:
	@echo "Zep LiveKit Integration Development Commands:"
	@echo ""
	@echo "Setup:"
	@echo "  make install     Install dependencies for development"
	@echo ""
	@echo "Development:"
	@echo "  make format      Format code with ruff"
	@echo "  make lint        Run linting checks"
	@echo "  make type-check  Run MyPy type checking"
	@echo "  make test        Run test suite"
	@echo ""
	@echo "Workflows:"
	@echo "  make pre-commit  Run pre-commit checks with auto-fixes"
	@echo "  make ci          Run strict CI-style checks"
	@echo "  make all         Run full development workflow"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean       Clean up build artifacts"

# Install dependencies
install:
	@echo "Installing dependencies..."
	pip install -e .[dev]

# Format code
format:
	@echo "Formatting code with ruff..."
	ruff format src/ tests/ examples/
	ruff check --fix src/ tests/ examples/

# Lint code
lint:
	@echo "Running linting checks..."
	ruff check src/ tests/ examples/

# Type checking
type-check:
	@echo "Running MyPy type checking..."
	mypy src/zep_livekit/

# Run tests
test:
	@echo "Running test suite..."
	@if [ -d tests/ ] && [ "$$(find tests/ -name '*.py' -type f | wc -l)" -gt 0 ]; then \
		pytest tests/ -v; \
	else \
		echo "No tests found, skipping test execution."; \
	fi

# Pre-commit workflow (with auto-fixes)
pre-commit: format lint type-check test
	@echo "✅ Pre-commit checks completed successfully!"

# CI workflow (strict, no auto-fixes)
ci:
	@echo "Running CI checks..."
	@echo "Checking code formatting..."
	ruff format --check src/ tests/ examples/
	@echo "Running linting..."
	ruff check src/ tests/ examples/
	@echo "Running type checking..."
	mypy src/zep_livekit/
	@echo "Running tests..."
	@if [ -d tests/ ] && [ "$$(find tests/ -name '*.py' -type f | wc -l)" -gt 0 ]; then \
		pytest tests/ -v --cov=src/zep_livekit --cov-report=term-missing; \
	else \
		echo "No tests found, skipping test execution."; \
	fi
	@echo "✅ All CI checks passed!"

# Full development workflow
all: install pre-commit
	@echo "✅ Full development workflow completed!"

# Clean up
clean:
	@echo "Cleaning up build artifacts..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete