#!/usr/bin/env bash
# Also works before setup: reports missing Python dependencies without bootstrapping.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${MACE_PYTHON:-}" && ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  export MACE_PYTHON="$(command -v "${MACE_SETUP_PYTHON:-python3}")"
fi
source "$ROOT_DIR/scripts/runtime.sh"
exec "$MACE_PYTHON" "$ROOT_DIR/setup/doctor.py" "$@"
