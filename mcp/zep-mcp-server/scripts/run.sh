#!/bin/bash
set -e

# Run script for Zep MCP Server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Check for .env file
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "Error: .env file not found in $PROJECT_DIR"
    echo ""
    echo "Please create a .env file with your Zep API key:"
    echo "  cp .env.example .env"
    echo "  # Edit .env and add your ZEP_API_KEY"
    echo ""
    exit 1
fi

# Load environment variables
set -a
source "$PROJECT_DIR/.env"
set +a

# Verify ZEP_API_KEY is set
if [ -z "$ZEP_API_KEY" ]; then
    echo "Error: ZEP_API_KEY not set in .env file"
    exit 1
fi

cd "$PROJECT_DIR"

echo "Starting Zep MCP Server with docker-compose..."
echo ""

docker-compose up
