#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./evaluation/run_compact_experiment.sh --scenario <name> --app <app_name> --runs <N> --export-root <dir> [--variants v1,v2,...] [--keep-local]

Purpose:
  Run one app at a time over an existing <scenario>__expanded tree, export only the
  minimal artifacts needed for later plotting, and delete local raw results after
  each variant unless --keep-local is used.

Examples:
  ./evaluation/run_compact_experiment.sh --scenario density_ip --app trickle --runs 3 --export-root /mnt/shared/pymace_density_today
  ./evaluation/run_compact_experiment.sh --scenario density_ip --app rapid --runs 3 --export-root /mnt/shared/pymace_density_today
  ./evaluation/run_compact_experiment.sh --scenario density_ip --app trickle --runs 3 --export-root /mnt/shared/pymace_density_today --variants count=10,count=20
EOF
}

SCENARIO=""
APP=""
RUNS=""
EXPORT_ROOT=""
VARIANTS_CSV=""
KEEP_LOCAL=false

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
    --export-root)
      EXPORT_ROOT="$2"
      shift 2
      ;;
    --variants)
      VARIANTS_CSV="$2"
      shift 2
      ;;
    --keep-local)
      KEEP_LOCAL=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SCENARIO" || -z "$APP" || -z "$RUNS" || -z "$EXPORT_ROOT" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXPANDED_SCENARIO_DIR="$ROOT_DIR/scenarios/${SCENARIO}__expanded"
LOCAL_SCENARIO_RESULTS_DIR="$ROOT_DIR/results/${SCENARIO}__expanded"
EXPORT_RESULTS_DIR="$EXPORT_ROOT/results"

if [[ ! -d "$EXPANDED_SCENARIO_DIR" ]]; then
  echo "[ERROR] Missing expanded scenario directory: $EXPANDED_SCENARIO_DIR" >&2
  echo "[ERROR] This helper expects pre-existing scenarios/${SCENARIO}__expanded/ variants." >&2
  exit 1
fi

mkdir -p "$EXPORT_RESULTS_DIR"

write_export_readme() {
  local readme_path="$EXPORT_ROOT/README_COMPACT_RESULTS.txt"

  cat >"$readme_path" <<'EOF'
This export contains compact benchmark artifacts only.

Preserved per variant/app:
- results/<scenario>__expanded/<variant>/scenario.json
- results/<scenario>__expanded/<variant>/variant_meta.json
- results/<scenario>__expanded/<variant>/<app>/summary.csv
- results/<scenario>__expanded/<variant>/<app>/run_*/coverage_nodes.csv

To generate aggregated CSVs and plots later from a full repo checkout:

  bash evaluation/gera_compact.sh

Do not use evaluation/gera.sh on this compact export, because gera.sh expects the
full raw run directories and tries to rebuild summary.csv from scratch.
EOF
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

try:
    data = json.loads(status_path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

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

cleanup_run_dir() {
  local run_dir="$1"

  find "$run_dir" -maxdepth 1 -type f \
    \( \
      -name 'node_*.pcap' -o \
      -name 'node_*.pcap.stderr' -o \
      -name 'node_*.net.log' -o \
      -name 'pymace.stdout.log' -o \
      -name 'pymace.stderr.log' -o \
      -name 'pcap_metrics.jsonl' -o \
      -name 'collect_logs_summary.json' -o \
      -name 'process_pcaps_summary.json' -o \
      -name 'mace.json' -o \
      -name 'node_config.json' \
    \) -delete
}

copy_variant_metadata() {
  local scenario_variant_dir="$1"
  local export_variant_dir="$2"

  mkdir -p "$export_variant_dir"

  cp "$scenario_variant_dir/scenario.json" "$export_variant_dir/scenario.json"
  if [[ -f "$scenario_variant_dir/variant_meta.json" ]]; then
    cp "$scenario_variant_dir/variant_meta.json" "$export_variant_dir/variant_meta.json"
  fi
}

export_variant_artifacts() {
  local variant_name="$1"
  local local_app_dir="$2"

  local scenario_variant_dir="$EXPANDED_SCENARIO_DIR/$variant_name"
  local export_variant_dir="$EXPORT_RESULTS_DIR/${SCENARIO}__expanded/$variant_name"
  local export_app_dir="$export_variant_dir/$APP"

  rm -rf "$export_app_dir"
  mkdir -p "$export_app_dir"

  copy_variant_metadata "$scenario_variant_dir" "$export_variant_dir"

  cp "$local_app_dir/summary.csv" "$export_app_dir/summary.csv"

  for run_dir in "$local_app_dir"/run_*; do
    [[ -d "$run_dir" ]] || continue
    if [[ -f "$run_dir/coverage_nodes.csv" ]]; then
      mkdir -p "$export_app_dir/$(basename "$run_dir")"
      cp "$run_dir/coverage_nodes.csv" "$export_app_dir/$(basename "$run_dir")/coverage_nodes.csv"
    fi
  done
}

variant_names() {
  if [[ -n "$VARIANTS_CSV" ]]; then
    tr ',' '\n' <<<"$VARIANTS_CSV"
  else
    find "$EXPANDED_SCENARIO_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V
  fi
}

write_export_readme

echo "=== Compact Experiment ==="
echo "Scenario     : $SCENARIO"
echo "App          : $APP"
echo "Runs         : $RUNS"
echo "Export root  : $EXPORT_ROOT"
echo "Keep local   : $KEEP_LOCAL"
echo "Expanded dir : $EXPANDED_SCENARIO_DIR"
echo "=========================="

while IFS= read -r VARIANT_NAME; do
  [[ -n "$VARIANT_NAME" ]] || continue

  SCENARIO_REF="${SCENARIO}__expanded/$VARIANT_NAME"
  LOCAL_VARIANT_DIR="$LOCAL_SCENARIO_RESULTS_DIR/$VARIANT_NAME"
  LOCAL_APP_DIR="$LOCAL_VARIANT_DIR/$APP"

  echo
  echo "=== Variant: $VARIANT_NAME | App: $APP ==="

  rm -rf "$LOCAL_APP_DIR"
  mkdir -p "$LOCAL_APP_DIR"

  for RUN in $(seq 1 "$RUNS"); do
    RUN_ID="$(printf "run_%03d" "$RUN")"
    RUN_DIR="$LOCAL_APP_DIR/$RUN_ID"
    RUN_STATUS_PATH="$RUN_DIR/run_status.json"

    echo "--- Running $SCENARIO_REF | $APP | $RUN_ID ---"
    "$ROOT_DIR/evaluation/run_scenario.sh" "$SCENARIO_REF" "$APP" "$RUN_ID"

    RUN_ANALYZABLE="$(read_run_field "$RUN_STATUS_PATH" "analyzable")"
    RUN_STATUS="$(read_run_field "$RUN_STATUS_PATH" "status")"

    echo "[INFO] Run status: ${RUN_STATUS:-unknown} | analyzable=${RUN_ANALYZABLE:-false}"

    if [[ "$RUN_ANALYZABLE" != "true" ]]; then
      echo "[ERROR] Non-analyzable run. Local data kept at: $RUN_DIR" >&2
      exit 1
    fi

    cleanup_run_dir "$RUN_DIR"
  done

  python3 "$ROOT_DIR/evaluation/parse_metrics.py" "$LOCAL_APP_DIR"

  if [[ ! -f "$LOCAL_APP_DIR/summary.csv" ]]; then
    echo "[ERROR] Missing summary.csv after parse_metrics: $LOCAL_APP_DIR/summary.csv" >&2
    exit 1
  fi

  export_variant_artifacts "$VARIANT_NAME" "$LOCAL_APP_DIR"
  echo "[INFO] Exported compact artifacts to $EXPORT_RESULTS_DIR/${SCENARIO}__expanded/$VARIANT_NAME/$APP"

  if [[ "$KEEP_LOCAL" != "true" ]]; then
    rm -rf "$LOCAL_VARIANT_DIR"
    rmdir "$LOCAL_SCENARIO_RESULTS_DIR" 2>/dev/null || true
  fi
done < <(variant_names)

echo
echo "[OK] Finished compact experiment for scenario=$SCENARIO app=$APP"
echo "[OK] Export root: $EXPORT_ROOT"
