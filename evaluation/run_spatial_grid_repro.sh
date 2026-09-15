#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUNS=10
START_RUN=1
POSITIONAL_RUNS_SEEN=0
JOBS_FILE="${JOBS_JSON:-evaluation/jobs_spatial_grid.json}"
STAMP="$(date +%Y%m%d_%H%M%S)"
CATALOG_MANIFEST="evaluation/trace_catalogs/spatial_grid_1km_20x20/catalog.json"

usage() {
  echo "Usage: ./evaluation/run_spatial_grid_repro.sh [runs]"
  echo "       ./evaluation/run_spatial_grid_repro.sh --runs N [--start-run K]"
  echo
  echo "Default: 10 runs per app/variant"
  echo "Examples:"
  echo "  ./evaluation/run_spatial_grid_repro.sh 5"
  echo "  ./evaluation/run_spatial_grid_repro.sh --runs 5 --start-run 6"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --runs)
      RUNS="$2"
      shift 2
      ;;
    --start-run)
      START_RUN="$2"
      shift 2
      ;;
    *)
      if (( POSITIONAL_RUNS_SEEN == 0 )); then
        RUNS="$1"
        POSITIONAL_RUNS_SEEN=1
        shift
      else
        echo "[ERROR] Unknown argument: $1" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || (( RUNS < 1 )); then
  echo "[ERROR] runs must be a positive integer: $RUNS" >&2
  exit 1
fi

if ! [[ "$START_RUN" =~ ^[0-9]+$ ]] || (( START_RUN < 1 )); then
  echo "[ERROR] --start-run must be a positive integer: $START_RUN" >&2
  exit 1
fi

END_RUN=$((START_RUN + RUNS - 1))
LOG="spatial_grid_run_${START_RUN}_to_${END_RUN}_${STAMP}.log"

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "[ERROR] jobs file not found: $JOBS_FILE" >&2
  exit 1
fi

if [[ ! -f "$CATALOG_MANIFEST" ]]; then
  echo "[ERROR] spatial trace catalog is missing" >&2
  echo "Run: python3 evaluation/generate_spatial_trace_catalog.py --force" >&2
  exit 1
fi

CATALOG_RUNS="$(python3 - "$CATALOG_MANIFEST" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
print(int(manifest.get("run_count", len(manifest.get("runs", [])))))
PY
)"
if (( END_RUN > CATALOG_RUNS )); then
  echo "[ERROR] requested run range ends at run_$(printf "%03d" "$END_RUN"), but catalog has only $CATALOG_RUNS runs" >&2
  exit 1
fi

exec > >(tee "$LOG") 2>&1

echo "=== Spatial grid reproducibility run ==="
echo "Repo      : $ROOT_DIR"
echo "Jobs      : $JOBS_FILE"
echo "Runs/job  : $RUNS"
echo "Run range : $(printf "run_%03d" "$START_RUN")..$(printf "run_%03d" "$END_RUN")"
echo "Log       : $LOG"
echo "Started   : $(date -Is)"
echo

echo "[INFO] Checking sudo access and refreshing credentials."
sudo -v

SUDO_KEEPALIVE_PID=""
cleanup() {
  if [[ -n "$SUDO_KEEPALIVE_PID" ]]; then
    kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

while true; do
  sudo -n true 2>/dev/null || true
  sleep 60
done &
SUDO_KEEPALIVE_PID="$!"

echo "[INFO] Fixing ownership of generated spatial directories when present."
sudo chown -R "$(id -un):$(id -gn)" \
  results/spatial_grid_ip__expanded \
  results/spatial_grid_batman__expanded \
  scenarios/spatial_grid_ip__expanded \
  scenarios/spatial_grid_batman__expanded 2>/dev/null || true

if (( START_RUN == 1 )); then
  mkdir -p results_archive
  for scenario in spatial_grid_ip__expanded spatial_grid_batman__expanded; do
    if [[ -d "results/$scenario" ]]; then
      archive_dir="results_archive/${scenario}_before_${STAMP}"
      echo "[INFO] Archiving results/$scenario to $archive_dir"
      mv "results/$scenario" "$archive_dir"
    fi
  done
else
  echo "[INFO] Preserving existing spatial results and appending the requested run range."
fi

echo
echo "[INFO] Running spatial grid experiments."
time ./evaluation/run_all.sh --jobs "$JOBS_FILE" --runs "$RUNS" --start-run "$START_RUN"

echo
echo "[INFO] Generating spatial plots and tables."
./evaluation/gera_spatial.sh

echo
echo "=== Finished ==="
echo "Finished : $(date -Is)"
echo "Tables   : results/tables/"
echo "Plots    : results/plots/spatial/"
echo "Log      : $LOG"
