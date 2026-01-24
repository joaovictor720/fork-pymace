#!/bin/bash
set -e

while [[ $# -gt 0 ]]; do
  case $1 in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --app) APP="$2"; shift 2 ;;
    --algorithm) APP="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$SCENARIO" || -z "$APP" || -z "$RUNS" ]]; then
  echo "Usage:"
  echo "./run_experiment.sh --scenario <name> --app <app_name> --runs N"
  echo "Alias: --algorithm <app_name>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO_JSON_PATH="$ROOT_DIR/scenarios/$SCENARIO/scenario.json"

if [[ ! -f "$SCENARIO_JSON_PATH" ]]; then
    echo "[ERROR] Scenario file not found: $SCENARIO_JSON_PATH"
    exit 1
fi

echo "=== Experiment ==="
echo "Scenario : $SCENARIO"
echo "App      : $APP"
echo "Runs     : $RUNS"
echo "==============="

VARIANTS=$(python evaluation/expand_experiments.py "$SCENARIO")

for VARIANT in $VARIANTS; do
  VARIANT_RESULTS_DIR="$ROOT_DIR/results/$VARIANT/$APP"
  VARIANT_SC_DIR="$ROOT_DIR/scenarios/$VARIANT"

  if [[ -d "$VARIANT_RESULTS_DIR" ]]; then
    echo "[INFO] Cleaning previous results in $VARIANT_RESULTS_DIR"
    rm -rf "$VARIANT_RESULTS_DIR"
  fi

  mkdir -p "$VARIANT_RESULTS_DIR"

  if [[ -f "$VARIANT_SC_DIR/variant_meta.json" ]]; then
    cp "$VARIANT_SC_DIR/variant_meta.json" "$VARIANT_RESULTS_DIR/variant_meta.json"
  fi

  for RUN in $(seq 1 "$RUNS"); do
    RUN_ID=$(printf "run_%03d" "$RUN")
    echo "--- Running $VARIANT | $RUN_ID ---"
    "$ROOT_DIR/evaluation/run_scenario.sh" "$VARIANT" "$APP" "$RUN_ID"
  done

  python "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"
done

echo "run_experiment.sh - EXPERIMENT FINISHED!"
