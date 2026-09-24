#!/usr/bin/env bash
# MANUAL: creates CORE namespaces and selects the signed experimental module.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/runtime.sh"
RUN_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
"$MACE_ROOT/evaluation/run_scenario.sh" batman_wifi_smoke broadcast "$RUN_ID"
"$MACE_PYTHON" "$MACE_ROOT/evaluation/validate_smoke.py" \
  "$MACE_ROOT/results/batman_wifi_smoke/broadcast/$RUN_ID" --wifi
