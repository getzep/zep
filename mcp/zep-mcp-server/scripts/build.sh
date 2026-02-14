#!/bin/bash
set -e

# Build script for Zep MCP Server Docker image

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
IMAGE_NAME="${IMAGE_NAME:-zep-mcp-server}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
VERSION="${VERSION:-0.1.0}"

echo "Building Docker image..."
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  Version: ${VERSION}"
echo ""

cd "$PROJECT_DIR"

docker build \
    --build-arg VERSION="${VERSION}" \
    --tag "${IMAGE_NAME}:${IMAGE_TAG}" \
    --file Dockerfile \
    .

echo ""
echo "Build complete: ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "To run the server:"
echo "  docker run -e ZEP_API_KEY=your-key-here -p 8080:8080 ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "Or use docker-compose:"
echo "  docker-compose up"
