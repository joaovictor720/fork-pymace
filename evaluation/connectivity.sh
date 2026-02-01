#!/bin/bash
set -euo pipefail

SCENARIO="topology_degree_ecdf"
APP="broadcast"

ROOT_DIR="results/${SCENARIO}/${APP}"
OUT_CSV="${ROOT_DIR}/degree_samples_all.csv"
OUT_PDF="results/plots/degree_ecdf.pdf"

python3 evaluation/compute_degree_sample.py \
  --runs_root "${ROOT_DIR}" \
  --out "${OUT_CSV}" \
  --range_m 160 \
  --bin_s 1.0 \
  --max_time_s 20

python3 evaluation/plot_degree_ecdf.py \
  --in_csv "${OUT_CSV}" \
  --out "${OUT_PDF}"
