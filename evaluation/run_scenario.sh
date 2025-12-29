#!/bin/bash
set -e

SCENARIO="$1"
ALGO="$2"
CONFIG="$3"
RUN_ID="$4"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
SCENARIO_SPEC="$SCENARIO_DIR/scenario.json"
MACE_JSON="$SCENARIO_DIR/mace.json"

NODE_CFG_BASE="$ROOT_DIR/apps/crdt/node-config/$CONFIG.json"
RESULT_DIR="$ROOT_DIR/results/$SCENARIO/$ALGO/$RUN_ID"
TMP_CFG="$RESULT_DIR/node_config.json"

mkdir -p "$RESULT_DIR"

# -------------------------------
# 0. Sanity checks
# -------------------------------
if [[ ! -f "$SCENARIO_SPEC" ]]; then
  echo "[ERROR] Scenario spec not found: $SCENARIO_SPEC"
  exit 1
fi

# -------------------------------
# 1. Generate mace.json from scenario.json
# -------------------------------
echo "[INFO] Generating mace.json from scenario.json"

python "$ROOT_DIR/evaluation/generate_scenario.py" \
  "$SCENARIO_DIR"

if [[ ! -f "$MACE_JSON" ]]; then
  echo "[ERROR] mace.json was not generated"
  exit 1
fi

# -------------------------------
# 2. Generate node-level config
# -------------------------------
echo "[INFO] Generating node_config.json"

python "$ROOT_DIR/evaluation/generate_node_config.py" \
  "$NODE_CFG_BASE" \
  "$RESULT_DIR" \
  "$TMP_CFG"

# -------------------------------
# 3. Select CRDT binary
# -------------------------------
if [[ "$ALGO" == "rapid" ]]; then
  BIN="$ROOT_DIR/apps/crdt/rapid/crdt_rapid"
elif [[ "$ALGO" == "broadcast" ]]; then
  BIN="$ROOT_DIR/apps/crdt/broadcast/crdt_broadcast"
else
  echo "[ERROR] Unknown algorithm: $ALGO"
  exit 1
fi

# -------------------------------
# 4. Export environment for MACE
# -------------------------------
export CRDT_BIN="$BIN"
export CRDT_NODE_CONFIG="$TMP_CFG"

echo "[INFO] Using binary: $CRDT_BIN"
echo "[INFO] Using node config: $CRDT_NODE_CONFIG"

# -------------------------------
# 5. Run scenario
# -------------------------------
echo "[INFO] Running MACE scenario: $SCENARIO"

sudo "$ROOT_DIR/pymace.py" -s "$MACE_JSON"

# -------------------------------
# 6. Collect logs
# -------------------------------
echo "[INFO] Collecting logs"

python "$ROOT_DIR/evaluation/collect_logs.py" "$RESULT_DIR"
