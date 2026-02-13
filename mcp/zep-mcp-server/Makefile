# Zep MCP Server Makefile

# Variables
BINARY_NAME=zep-mcp-server
BUILD_DIR=bin
CMD_DIR=cmd/server
MAIN_PACKAGE=./$(CMD_DIR)

# Go commands
GOCMD=go
GOBUILD=$(GOCMD) build
GOTEST=$(GOCMD) test
GOMOD=$(GOCMD) mod
GOVET=$(GOCMD) vet
GOFMT=gofmt

# Linting
GOLINT=golangci-lint

# Build flags
LDFLAGS=-ldflags "-s -w"
BUILD_FLAGS=-trimpath

# Docker
DOCKER_IMAGE=zep-mcp-server
DOCKER_TAG=latest

.PHONY: all build test lint fmt vet clean install-tools help docker-build docker-run docker-down docker-clean

# Default target
all: clean lint test build

## help: Display this help message
help:
	@echo "Zep MCP Server - Available targets:"
	@echo ""
	@echo "  make build         - Build the server binary"
	@echo "  make test          - Run all tests"
	@echo "  make test-verbose  - Run tests with verbose output"
	@echo "  make test-coverage - Run tests with coverage report"
	@echo "  make lint          - Run golangci-lint"
	@echo "  make fmt           - Format code with gofmt"
	@echo "  make vet           - Run go vet"
	@echo "  make clean         - Remove build artifacts"
	@echo "  make install-tools - Install development tools"
	@echo "  make all           - Run clean, lint, test, and build"
	@echo ""
	@echo "Docker targets:"
	@echo "  make docker-build  - Build Docker image"
	@echo "  make docker-run    - Run server with docker-compose"
	@echo "  make docker-down   - Stop docker-compose"
	@echo "  make docker-clean  - Remove Docker images and containers"
	@echo ""

## build: Build the server binary
build:
	@echo "Building $(BINARY_NAME)..."
	@mkdir -p $(BUILD_DIR)
	$(GOBUILD) $(BUILD_FLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME) $(MAIN_PACKAGE)
	@echo "✓ Build complete: $(BUILD_DIR)/$(BINARY_NAME)"

## test: Run all tests
test:
	@echo "Running tests..."
	$(GOTEST) -race -timeout 30s ./cmd/... ./internal/... ./pkg/...
	@echo "✓ Tests complete"

## test-verbose: Run tests with verbose output
test-verbose:
	@echo "Running tests (verbose)..."
	$(GOTEST) -v -race -timeout 30s ./...

## test-coverage: Run tests with coverage report
test-coverage:
	@echo "Running tests with coverage..."
	$(GOTEST) -race -coverprofile=coverage.out -covermode=atomic ./...
	$(GOCMD) tool cover -html=coverage.out -o coverage.html
	@echo "✓ Coverage report generated: coverage.html"

## lint: Run golangci-lint
lint:
	@echo "Running golangci-lint..."
	$(GOLINT) run --timeout 5m ./cmd/... ./internal/... ./pkg/...
	@echo "✓ Linting complete"

## lint-fix: Run golangci-lint with auto-fix
lint-fix:
	@echo "Running golangci-lint with auto-fix..."
	$(GOLINT) run --fix --timeout 5m ./...
	@echo "✓ Linting complete"

## fmt: Format code with gofmt
fmt:
	@echo "Formatting code..."
	@$(GOFMT) -s -w .
	@echo "✓ Formatting complete"

## vet: Run go vet
vet:
	@echo "Running go vet..."
	$(GOVET) ./cmd/... ./internal/... ./pkg/...
	@echo "✓ Vet complete"

## clean: Remove build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@rm -rf $(BUILD_DIR)
	@rm -f coverage.out coverage.html
	@echo "✓ Clean complete"

## install-tools: Install development tools
install-tools:
	@echo "Installing development tools..."
	@which golangci-lint > /dev/null || (echo "Installing golangci-lint..." && \
		go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest)
	@echo "✓ Tools installed"

## mod-tidy: Tidy go.mod and go.sum
mod-tidy:
	@echo "Tidying go modules..."
	$(GOMOD) tidy
	@echo "✓ Modules tidied"

## mod-verify: Verify module dependencies
mod-verify:
	@echo "Verifying module dependencies..."
	$(GOMOD) verify
	@echo "✓ Dependencies verified"

## check: Run all checks (fmt, vet, lint, test)
check: fmt vet lint test
	@echo "✓ All checks passed"

## docker-build: Build Docker image
docker-build:
	@echo "Building Docker image $(DOCKER_IMAGE):$(DOCKER_TAG)..."
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) .
	@echo "✓ Docker image built: $(DOCKER_IMAGE):$(DOCKER_TAG)"

## docker-run: Run server with docker-compose
docker-run:
	@echo "Starting server with docker-compose..."
	docker-compose up

## docker-run-detached: Run server with docker-compose in background
docker-run-detached:
	@echo "Starting server with docker-compose (detached)..."
	docker-compose up -d
	@echo "✓ Server started in background"

## docker-down: Stop docker-compose
docker-down:
	@echo "Stopping docker-compose..."
	docker-compose down
	@echo "✓ Stopped"

## docker-logs: View docker-compose logs
docker-logs:
	docker-compose logs -f

## docker-clean: Remove Docker images and containers
docker-clean:
	@echo "Cleaning Docker resources..."
	@docker-compose down -v 2>/dev/null || true
	@docker rmi $(DOCKER_IMAGE):$(DOCKER_TAG) 2>/dev/null || true
	@echo "✓ Docker resources cleaned"
