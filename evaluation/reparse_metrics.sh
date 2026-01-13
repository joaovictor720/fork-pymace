#!/bin/bash
set -e

while [[ $# -gt 0 ]]; do
  case $1 in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --algorithm) ALGO="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$SCENARIO" || -z "$ALGO" ]]; then
  echo "Usage:"
  echo "./reparse_metrics.sh --scenario <name> --algorithm <rapid|broadcast>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Re-parsing existing results ==="
echo "Scenario : $SCENARIO"
echo "Algorithm: $ALGO"
echo "==================================="

# Expande variantes do cenário
VARIANTS=$(python "$ROOT_DIR/evaluation/expand_experiments.py" "$SCENARIO")

for VARIANT in $VARIANTS; do
  VARIANT_RESULTS_DIR="$ROOT_DIR/results/$VARIANT/$ALGO"

  if [[ ! -d "$VARIANT_RESULTS_DIR" ]]; then
    echo "[WARN] Directory not found, skipping: $VARIANT_RESULTS_DIR"
    continue
  fi

  echo "--- Reprocessing $VARIANT | $ALGO ---"
  python "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"

done

echo "=== Done reprocessing ==="
