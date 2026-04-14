#!/bin/bash
set -uo pipefail

usage() {
  echo "Usage:"
  echo "./run_experiment.sh --scenario <name> --app <app_name> --runs N [--keep-going]"
  echo "Alias: --algorithm <app_name>"
}

KEEP_GOING=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SCENARIO="$2"
      shift 2
      ;;
    --app|--algorithm)
      APP="$2"
      shift 2
      ;;
    --runs)
      RUNS="$2"
      shift 2
      ;;
    --keep-going)
      KEEP_GOING=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${SCENARIO:-}" || -z "${APP:-}" || -z "${RUNS:-}" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO_JSON_PATH="$ROOT_DIR/scenarios/$SCENARIO/scenario.json"

if [[ ! -f "$SCENARIO_JSON_PATH" ]]; then
  echo "[ERROR] Scenario file not found: $SCENARIO_JSON_PATH"
  exit 1
fi

prepare_variant_results_dir() {
  local variant_results_dir="$1"

  if [[ -d "$variant_results_dir" ]]; then
    echo "[INFO] Cleaning previous results in $variant_results_dir"
    rm -rf "$variant_results_dir"
  fi

  mkdir -p "$variant_results_dir"
}

copy_variant_metadata() {
  local variant_scenario_dir="$1"
  local variant_results_dir="$2"

  if [[ -f "$variant_scenario_dir/scenario.json" ]]; then
    cp "$variant_scenario_dir/scenario.json" "$variant_results_dir/scenario.json"
  fi

  if [[ -f "$variant_scenario_dir/variant_meta.json" ]]; then
    cp "$variant_scenario_dir/variant_meta.json" "$variant_results_dir/variant_meta.json"
  fi
}

write_variant_status() {
  local variant="$1"
  local app="$2"
  local variant_results_dir="$3"
  shift 3

  python3 - "$variant" "$app" "$variant_results_dir" "$@" <<'PY'
import json
import sys
from pathlib import Path

variant = sys.argv[1]
app = sys.argv[2]
variant_results_dir = Path(sys.argv[3])
expected_runs = sys.argv[4:]

status_counts = {}
runs = []
missing_status_files = []
found_runs = []
analyzable_runs = 0

for run_id in expected_runs:
    run_dir = variant_results_dir / run_id
    status_path = run_dir / "run_status.json"
    if not status_path.exists():
        missing_status_files.append(run_id)
        continue

    found_runs.append(run_id)

    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        missing_status_files.append(run_id)
        continue

    run_status = status.get("status", "unknown")
    analyzable = bool(status.get("analyzable", False))
    pymace_rc = status.get("pymace", {}).get("rc")

    status_counts[run_status] = status_counts.get(run_status, 0) + 1
    if analyzable:
        analyzable_runs += 1

    runs.append(
        {
            "run_id": run_id,
            "status": run_status,
            "analyzable": analyzable,
            "pymace_rc": pymace_rc,
        }
    )

all_analyzable = len(found_runs) == len(expected_runs) and analyzable_runs == len(expected_runs)

payload = {
    "variant": variant,
    "app": app,
    "expected_runs": expected_runs,
    "found_runs": found_runs,
    "missing_status_files": missing_status_files,
    "status_counts": status_counts,
    "analyzable_runs": analyzable_runs,
    "all_analyzable": all_analyzable,
    "runs": runs,
}

out_path = variant_results_dir / "variant_status.json"
out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("true" if all_analyzable else "false")
PY
}

read_run_field() {
  local status_path="$1"
  local field="$2"

  python3 - "$status_path" "$field" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
field = sys.argv[2]

if not status_path.exists():
    print("")
    raise SystemExit(0)

data = json.loads(status_path.read_text(encoding="utf-8"))
value = data
for part in field.split("."):
    if isinstance(value, dict) and part in value:
        value = value[part]
    else:
        print("")
        raise SystemExit(0)

if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
PY
}

echo "=== Experiment ==="
echo "Scenario    : $SCENARIO"
echo "App         : $APP"
echo "Runs        : $RUNS"
echo "Keep going  : $KEEP_GOING"
echo "==============="

mapfile -t VARIANTS < <(python3 "$ROOT_DIR/evaluation/expand_experiments.py" "$SCENARIO")

if [[ ${#VARIANTS[@]} -eq 0 ]]; then
  echo "[ERROR] No variants returned for scenario: $SCENARIO"
  exit 1
fi

for VARIANT in "${VARIANTS[@]}"; do
  VARIANT_RESULTS_DIR="$ROOT_DIR/results/$VARIANT/$APP"
  VARIANT_SC_DIR="$ROOT_DIR/scenarios/$VARIANT"

  prepare_variant_results_dir "$VARIANT_RESULTS_DIR"
  copy_variant_metadata "$VARIANT_SC_DIR" "$VARIANT_RESULTS_DIR"

  EXPECTED_RUN_IDS=()
  for RUN in $(seq 1 "$RUNS"); do
    EXPECTED_RUN_IDS+=("$(printf "run_%03d" "$RUN")")
  done

  for RUN_ID in "${EXPECTED_RUN_IDS[@]}"; do
    RUN_STATUS_PATH="$VARIANT_RESULTS_DIR/$RUN_ID/run_status.json"

    echo "--- Running $VARIANT | $RUN_ID ---"
    "$ROOT_DIR/evaluation/run_scenario.sh" "$VARIANT" "$APP" "$RUN_ID" --keep-going

    write_variant_status "$VARIANT" "$APP" "$VARIANT_RESULTS_DIR" "${EXPECTED_RUN_IDS[@]}" >/dev/null

    if [[ ! -f "$RUN_STATUS_PATH" ]]; then
      echo "[ERROR] Missing run status file: $RUN_STATUS_PATH"
      if [[ "$KEEP_GOING" != "true" ]]; then
        exit 1
      fi
      continue
    fi

    RUN_ANALYZABLE="$(read_run_field "$RUN_STATUS_PATH" "analyzable")"
    RUN_STATUS="$(read_run_field "$RUN_STATUS_PATH" "status")"

    echo "[INFO] Run status: ${RUN_STATUS:-unknown} | analyzable=${RUN_ANALYZABLE:-false}"

    if [[ "$RUN_ANALYZABLE" != "true" && "$KEEP_GOING" != "true" ]]; then
      echo "[ERROR] Aborting after non-analyzable run: $VARIANT / $RUN_ID"
      exit 1
    fi
  done

  ALL_ANALYZABLE="$(write_variant_status "$VARIANT" "$APP" "$VARIANT_RESULTS_DIR" "${EXPECTED_RUN_IDS[@]}")"

  if [[ "$ALL_ANALYZABLE" == "true" ]]; then
    python3 "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"
  else
    echo "[INFO] Skipping summary.csv for $VARIANT / $APP because not all runs are analyzable."
  fi
done

echo "run_experiment.sh - EXPERIMENT FINISHED!"
