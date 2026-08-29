#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUNS="${1:-5}"
JOBS_FILE="${JOBS_JSON:-evaluation/jobs_article_repro.json}"

if [[ "$RUNS" == "-h" || "$RUNS" == "--help" ]]; then
  echo "Usage: ./evaluation/run_article_repro.sh [runs]"
  echo
  echo "Default: 5 runs per app/variant"
  echo "Example: ./evaluation/run_article_repro.sh 5"
  echo "Example: ./evaluation/run_article_repro.sh 10"
  exit 0
fi

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || (( RUNS < 1 )); then
  echo "[ERROR] runs must be a positive integer: $RUNS" >&2
  exit 1
fi

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "[ERROR] jobs file not found: $JOBS_FILE" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="article_repro_${RUNS}runs_${STAMP}.log"

exec > >(tee "$LOG") 2>&1

echo "=== Article reproducibility run ==="
echo "Repo      : $ROOT_DIR"
echo "Jobs      : $JOBS_FILE"
echo "Runs/job  : $RUNS"
echo "Log       : $LOG"
echo "Started   : $(date -Is)"
echo

echo "[INFO] Checking sudo access and refreshing credentials."
sudo -v

echo "[INFO] Fixing ownership of generated experiment directories when present."
sudo chown -R "$(id -un):$(id -gn)" \
  results \
  scenarios/density_ip__expanded \
  scenarios/density_batman__expanded 2>/dev/null || true

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

mkdir -p results_archive
if [[ -d results ]]; then
  ARCHIVE_DIR="results_archive/results_before_article_repro_${STAMP}"
  echo "[INFO] Archiving current results to $ARCHIVE_DIR"
  mv results "$ARCHIVE_DIR"
fi
mkdir -p results

echo
echo "[INFO] Running experiments."
time ./evaluation/run_all.sh --jobs "$JOBS_FILE" --runs "$RUNS"

echo
echo "[INFO] Generating article plots."
JOBS_JSON="$JOBS_FILE" ./evaluation/gera.sh

echo
echo "[INFO] Auditing reproducibility manifests."
python evaluation/repro_audit.py \
  --jobs "$JOBS_FILE" \
  --strict \
  --output results/article_repro_audit.csv

echo
echo "=== Finished ==="
echo "Finished : $(date -Is)"
echo "Plots    : results/plots/"
echo "Audit    : results/article_repro_audit.csv"
echo "Log      : $LOG"
