#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="${1:-$ROOT_DIR/results}"
PLOTS_DIR="${2:-$RESULTS_DIR/plots/spatial}"

cd "$ROOT_DIR"
python3 evaluation/spatial_results.py \
  --results-root "$RESULTS_DIR" \
  --output-dir "$RESULTS_DIR" \
  --strict

if [[ -f "$RESULTS_DIR/aggregated_spatial_car.csv" ]]; then
  python3 evaluation/plot_spatial_coverage.py \
    --input-dir "$RESULTS_DIR" \
    --output-dir "$PLOTS_DIR"
fi
