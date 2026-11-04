#!/usr/bin/env bash
set -euo pipefail

package_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gym_set="${1:-0}"

if ! [[ "$gym_set" =~ ^[0-9]+$ ]]; then
  echo "Usage: $0 [GYM_SET_INDEX]" >&2
  exit 2
fi

cd "$package_root"
exec uv run --frozen python scripts/start_gym_service.py --domain itsm --gym-set "$gym_set"
