#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

rm -f results/all_results.csv results/all_capacity_samples.csv results/aggregated_results.csv
rm -f results/plots/*.png results/plots/*.pdf

python3 evaluation/collect_all_results.py

if [[ -f all_results.csv ]]; then
  mv all_results.csv results/
else
  echo "[ERROR] collect_all_results.py did not produce all_results.csv" >&2
  exit 1
fi

if [[ -f all_capacity_samples.csv ]]; then
  mv all_capacity_samples.csv results/
else
  echo "[WARN] collect_all_results.py did not produce all_capacity_samples.csv"
fi

python3 evaluation/aggregate_results.py results/all_results.csv results/aggregated_results.csv
python3 evaluation/plot_results.py

echo "[OK] Compact aggregation and plotting finished."
