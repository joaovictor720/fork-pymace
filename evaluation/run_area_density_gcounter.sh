#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUNS="${1:-10}"
START_RUN="${2:-1}"
JOBS_FILE="${JOBS_JSON:-evaluation/jobs_area_density_gcounter.json}"

if [[ "$RUNS" == "-h" || "$RUNS" == "--help" ]]; then
  echo "Usage: ./evaluation/run_area_density_gcounter.sh [runs] [start_run]"
  echo
  echo "Default: 10 runs per app/variant, starting at run_001"
  echo "Example: ./evaluation/run_area_density_gcounter.sh 10"
  echo "Example: ./evaluation/run_area_density_gcounter.sh 5 6"
  exit 0
fi

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || (( RUNS < 1 )); then
  echo "[ERROR] runs must be a positive integer: $RUNS" >&2
  exit 1
fi

if ! [[ "$START_RUN" =~ ^[0-9]+$ ]] || (( START_RUN < 1 )); then
  echo "[ERROR] start_run must be a positive integer: $START_RUN" >&2
  exit 1
fi

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "[ERROR] jobs file not found: $JOBS_FILE" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="area_density_gcounter_${RUNS}runs_from$(printf "run_%03d" "$START_RUN")_${STAMP}.log"

exec > >(tee "$LOG") 2>&1

echo "=== Fixed-swarm density GCounter run ==="
echo "Repo      : $ROOT_DIR"
echo "Jobs      : $JOBS_FILE"
echo "Runs/job  : $RUNS"
echo "Start run : $(printf "run_%03d" "$START_RUN")"
echo "Log       : $LOG"
echo "Started   : $(date -Is)"
echo

PPRZLINK_PYTHON="${PPRZLINK_PYTHON:-/home/mace/Documents/pprzlink/lib/v2.0/python}"
if [[ -d "$PPRZLINK_PYTHON" ]]; then
  export PYTHONPATH="$PPRZLINK_PYTHON:${PYTHONPATH:-}"
  echo "PYTHONPATH : $PPRZLINK_PYTHON"
else
  echo "[WARN] pprzlink path not found: $PPRZLINK_PYTHON"
fi

echo "[INFO] Checking sudo access and refreshing credentials."
sudo -v

echo "[INFO] Fixing ownership of generated experiment directories when present."
sudo chown -R "$(id -un):$(id -gn)" \
  results \
  scenarios/area_ip__expanded \
  scenarios/area_batman__expanded 2>/dev/null || true

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

mkdir -p results

echo
echo "[INFO] Running experiments."
time ./evaluation/run_all.sh \
  --jobs "$JOBS_FILE" \
  --runs "$RUNS" \
  --start-run "$START_RUN"

echo
echo "[INFO] Generating GCounter plots."
JOBS_JSON="$JOBS_FILE" ./evaluation/gera.sh

echo
echo "=== Finished ==="
echo "Finished : $(date -Is)"
echo "Plots    : results/plots/"
echo "Log      : $LOG"
