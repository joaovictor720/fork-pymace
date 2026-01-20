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
SCENARIO_JSON_PATH="$ROOT_DIR/scenarios/$SCENARIO/scenario.json"

# Verifica se o arquivo de cenário existe antes de começar
if [[ ! -f "$SCENARIO_JSON_PATH" ]]; then
    echo "[ERROR] Scenario file not found: $SCENARIO_JSON_PATH"
    exit 1
fi

if [[ -d "$RESULTS_DIR" ]]; then
  echo "[INFO] Cleaning previous results in $RESULTS_DIR"
  rm -rf "$RESULTS_DIR"
fi

mkdir -p "$RESULTS_DIR"

echo "=== Experiment ==="
echo "Scenario : $SCENARIO"
echo "Algorithm: $ALGO"
echo "Runs     : $RUNS"
echo "==============="

VARIANTS=$(python evaluation/expand_experiments.py "$SCENARIO")

for VARIANT in $VARIANTS; do
  VARIANT_RESULTS_DIR="$ROOT_DIR/results/$VARIANT/$ALGO"

  if [[ -d "$VARIANT_RESULTS_DIR" ]]; then
    echo "[INFO] Cleaning previous results in $VARIANT_RESULTS_DIR"
    rm -rf "$VARIANT_RESULTS_DIR"
  fi

  mkdir -p "$VARIANT_RESULTS_DIR"

  for RUN in $(seq 1 "$RUNS"); do
    RUN_ID=$(printf "run_%03d" "$RUN")
    echo "--- Running $VARIANT | $RUN_ID ---"

    "$ROOT_DIR/evaluation/run_scenario.sh" "$VARIANT" "$ALGO" "$RUN_ID"
  done

  python "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"
done

echo "run_experiment.sh - EXPERIMENT FINISHED!"