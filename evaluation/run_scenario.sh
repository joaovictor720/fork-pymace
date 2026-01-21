#!/bin/bash
set -e

SCENARIO="$1"
ALGO="$2"
RUN_ID="$3"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
SCENARIO_SPEC="$SCENARIO_DIR/scenario.json"
MACE_JSON="$SCENARIO_DIR/mace.json"

RESULT_DIR="$ROOT_DIR/results/$SCENARIO/$ALGO/$RUN_ID/"
NODE_CFG="$RESULT_DIR/node_config.json"

mkdir -p "$RESULT_DIR"

# -------------------------------
# Sanity check
# -------------------------------
[[ -f "$SCENARIO_SPEC" ]] || {
  echo "[ERROR] Missing scenario.json"
  exit 1
}

# -------------------------------
# Generate mace.json
# -------------------------------
python "$ROOT_DIR/evaluation/generate_scenario.py" "$SCENARIO_DIR" "$ALGO"

# -------------------------------
# Generate node_config.json
# -------------------------------
python "$ROOT_DIR/evaluation/generate_node_config.py" \
  "$SCENARIO_SPEC" \
  "$NODE_CFG" \
  "$RESULT_DIR"

# -------------------------------
# Select binary
# -------------------------------
case "$ALGO" in
  rapid) BIN="$ROOT_DIR/apps/crdt/rapid/crdt_rapid" ;;
  broadcast) BIN="$ROOT_DIR/apps/crdt/broadcast/crdt_broadcast" ;;
  *) echo "[ERROR] Unknown algorithm"; exit 1 ;;
esac

export CRDT_BIN="$BIN"
export CRDT_NODE_CONFIG="$NODE_CFG"

# -------------------------------
# Inject paths
# -------------------------------
sed -i \
  -e "s|__CRDT_BIN__|$BIN|g" \
  -e "s|__CRDT_NODE_CONFIG__|$NODE_CFG|g" \
  "$MACE_JSON"

# -------------------------------
# Run
# -------------------------------
sudo "$ROOT_DIR/pymace.py" -s "$MACE_JSON" || true

# -------------------------------
# Collect logs
# -------------------------------
python "$ROOT_DIR/evaluation/collect_logs.py" "$RESULT_DIR"

# -------------------------------
# Post-process pcaps (host-side) and purge
# -------------------------------
python "$ROOT_DIR/evaluation/process_pcaps.py" "$RESULT_DIR" "$ALGO" --append-netlog --delete
