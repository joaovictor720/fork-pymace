#!/bin/bash
set -euo pipefail

CONFIG="${1:-scenarios/reference_point_group_topology/scenario.json}"
OUT_DIR="${2:-results/reference_point_group_topology}"

python3 evaluation/analyze_reference_point_group.py \
  --config "${CONFIG}" \
  --out-dir "${OUT_DIR}"
