# Makefile for the Zep Google ADK (Go) integration

.PHONY: help tidy fmt fmt-check vet lint test test-race build example all ci clean

help:
	@echo "Available commands:"
	@echo "  tidy       - go mod tidy"
	@echo "  fmt        - Format code with gofmt"
	@echo "  fmt-check  - Verify code is gofmt-clean (fails if not)"
	@echo "  vet        - Run go vet"
	@echo "  lint       - Run golangci-lint (if installed)"
	@echo "  test       - Run tests"
	@echo "  test-race  - Run tests with the race detector"
	@echo "  build      - Build all packages"
	@echo "  example    - Run the example (needs ZEP_API_KEY + GOOGLE_API_KEY)"
	@echo "  all        - tidy + fmt-check + vet + lint + test"
	@echo "  ci         - vet + lint + test (no auto-formatting)"
	@echo "  clean      - go clean"

tidy:
	go mod tidy

fmt:
	gofmt -w .

fmt-check:
	@unformatted=$$(gofmt -l .); \
	if [ -n "$$unformatted" ]; then \
		echo "These files are not gofmt-clean:"; echo "$$unformatted"; exit 1; \
	fi

vet:
	go vet ./...

# Runs golangci-lint when available; skips with a notice otherwise.
lint:
	@if command -v golangci-lint >/dev/null 2>&1; then \
		golangci-lint run ./...; \
	else \
		echo "golangci-lint not installed; skipping (install: https://golangci-lint.run)"; \
	fi

test:
	go test ./...

test-race:
	go test -race ./...

build:
	go build ./...

example:
	go run ./examples

all: tidy fmt-check vet lint test
	@echo "All checks passed!"

ci: vet lint test
	@echo "CI checks passed!"

clean:
	go clean ./...
