#!/usr/bin/env bash
# Idempotent bootstrap for the Zep examples & integrations monorepo.
# Runs after the repository is checked out. Safe to run repeatedly.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# --- Python tooling: uv --------------------------------------------------
# The astral.sh installer host is not in the Cloud Agent egress allowlist, so
# install uv from PyPI (which is allowed) instead of the curl|sh installer.
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --user --upgrade uv
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

# --- Go: build the Zep MCP server ---------------------------------------
# Primes the Go module cache and produces bin/zep-mcp-server.
( cd mcp/zep-mcp-server && make build )

# --- Python: zep-ingest ingestion package -------------------------------
# Creates .venv and installs the package with dev extras.
( cd ingestion && uv sync --extra dev )

echo "Environment bootstrap complete."
