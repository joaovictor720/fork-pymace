#!/bin/bash
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/runtime.sh"
set -e

SCENARIO=""
APP=""
RUNS=""
START_RUN=1
CLEAN_RESULTS=1

while [[ $# -gt 0 ]]; do
  case $1 in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --app) APP="$2"; shift 2 ;;
    --algorithm) APP="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --start-run) START_RUN="$2"; shift 2 ;;
    --append|--keep-existing) CLEAN_RESULTS=0; shift ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$SCENARIO" || -z "$APP" || -z "$RUNS" ]]; then
  echo "Usage:"
  echo "./run_experiment.sh --scenario <name> --app <app_name> --runs N [--start-run K]"
  echo "Alias: --algorithm <app_name>"
  exit 1
fi

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || (( RUNS < 1 )); then
  echo "[ERROR] --runs must be a positive integer: $RUNS"
  exit 1
fi

if ! [[ "$START_RUN" =~ ^[0-9]+$ ]] || (( START_RUN < 1 )); then
  echo "[ERROR] --start-run must be a positive integer: $START_RUN"
  exit 1
fi

END_RUN=$((START_RUN + RUNS - 1))
if (( START_RUN > 1 )); then
  CLEAN_RESULTS=0
fi

ROOT_DIR="$MACE_ROOT"
cd "$ROOT_DIR"
SCENARIO_JSON_PATH="$ROOT_DIR/scenarios/$SCENARIO/scenario.json"

if [[ ! -f "$SCENARIO_JSON_PATH" ]]; then
    echo "[ERROR] Scenario file not found: $SCENARIO_JSON_PATH"
    exit 1
fi

echo "=== Experiment ==="
echo "Scenario : $SCENARIO"
echo "App      : $APP"
echo "Runs     : $RUNS"
echo "Run range: $(printf "run_%03d" "$START_RUN")..$(printf "run_%03d" "$END_RUN")"
echo "==============="

VARIANTS=$(python evaluation/expand_experiments.py "$SCENARIO")

for VARIANT in $VARIANTS; do
  VARIANT_RESULTS_DIR="$ROOT_DIR/results/$VARIANT/$APP"
  VARIANT_SC_DIR="$ROOT_DIR/scenarios/$VARIANT"

  if [[ -d "$VARIANT_RESULTS_DIR" && "$CLEAN_RESULTS" == "1" ]]; then
    echo "[INFO] Cleaning previous results in $VARIANT_RESULTS_DIR"
    rm -rf "$VARIANT_RESULTS_DIR"
  elif [[ -d "$VARIANT_RESULTS_DIR" ]]; then
    echo "[INFO] Preserving previous results in $VARIANT_RESULTS_DIR"
  fi

  mkdir -p "$VARIANT_RESULTS_DIR"

  if [[ -f "$VARIANT_SC_DIR/variant_meta.json" ]]; then
    cp "$VARIANT_SC_DIR/variant_meta.json" "$VARIANT_RESULTS_DIR/variant_meta.json"
  fi

  for RUN in $(seq "$START_RUN" "$END_RUN"); do
    RUN_ID=$(printf "run_%03d" "$RUN")
    RUN_RESULTS_DIR="$VARIANT_RESULTS_DIR/$RUN_ID"
    if [[ -d "$RUN_RESULTS_DIR" ]]; then
      echo "[INFO] Cleaning previous run in $RUN_RESULTS_DIR"
      rm -rf "$RUN_RESULTS_DIR"
    fi
    echo "--- Running $VARIANT | $RUN_ID ---"
    "$ROOT_DIR/evaluation/run_scenario.sh" "$VARIANT" "$APP" "$RUN_ID"
  done

  python "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"
done

echo "run_experiment.sh - EXPERIMENT FINISHED!"
