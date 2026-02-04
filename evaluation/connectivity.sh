#!/bin/bash
set -euo pipefail

SCENARIO="topology_degree_ecdf__expanded"
APP="broadcast"

COUNTS=(10 20 30 40 50)

RANGE_M=160
BIN_S=1.0
MAX_TIME_S=20

OUT_DIR="results/plots"
OUT_PREFIX="${OUT_DIR}/topology_connectivity"

mkdir -p "${OUT_DIR}"

IN_CSVS=()

for COUNT in "${COUNTS[@]}"; do
  ROOT_DIR="results/${SCENARIO}/count=${COUNT}/${APP}"
  OUT_CSV="${ROOT_DIR}/degree_samples_all.csv"

  python3 evaluation/compute_degree_sample.py \
    --runs_root "${ROOT_DIR}" \
    --out "${OUT_CSV}" \
    --range_m "${RANGE_M}" \
    --bin_s "${BIN_S}" \
    --max_time_s "${MAX_TIME_S}"

  IN_CSVS+=("${OUT_CSV}")
done

python3 evaluation/plot_degree_ecdf.py \
  --in_csv "${IN_CSVS[@]}" \
  --out_prefix "${OUT_PREFIX}" \
  --max_xticks 9 \
  --extend_ecdf_to_global_x \
  --plot_components_count
