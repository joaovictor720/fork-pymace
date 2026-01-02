#!/bin/bash
set -e

while [[ $# -gt 0 ]]; do
  case $1 in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --algorithm) ALGO="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$SCENARIO" || -z "$ALGO" || -z "$RUNS" ]]; then
  echo "Usage:"
  echo "./run_experiment.sh --scenario <name> --algorithm <rapid|broadcast> --runs N"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="$ROOT_DIR/results/$SCENARIO/$ALGO"

mkdir -p "$RESULTS_DIR"

echo "=== Experiment ==="
echo "Scenario : $SCENARIO"
echo "Algorithm: $ALGO"
echo "Runs     : $RUNS"
echo "==============="

for RUN in $(seq 1 "$RUNS"); do
  RUN_ID=$(printf "run_%03d" "$RUN")
  echo "--- Running $RUN_ID ---"

  "$ROOT_DIR/evaluation/run_scenario.sh" \
    "$SCENARIO" \
    "$ALGO" \
    "$RUN_ID"
done

python "$ROOT_DIR/evaluation/parse_metrics.py" "$RESULTS_DIR"
