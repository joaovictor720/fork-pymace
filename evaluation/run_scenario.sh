#!/bin/bash
set -e

SCENARIO="$1"
APP="$2"
RUN_ID="$3"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
SCENARIO_SPEC="$SCENARIO_DIR/scenario.json"
MACE_JSON="$SCENARIO_DIR/mace.json"

RESULT_DIR="$ROOT_DIR/results/$SCENARIO/$APP/$RUN_ID/"
NODE_CFG="$RESULT_DIR/node_config.json"

mkdir -p "$RESULT_DIR"

# -------------------------------
# Sanity check
# -------------------------------
[[ -f "$SCENARIO_SPEC" ]] || {
  echo "[ERROR] Missing scenario.json: $SCENARIO_SPEC"
  exit 1
}

# -------------------------------
# Select batman-adv hard-interface behavior on the host
# -------------------------------
BATMAN_CONFIG="$(
  python3 - "$SCENARIO_SPEC" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    scenario = json.load(stream)

network = scenario.get("network", {})
routing = str(network.get("routing", "none")).strip().lower()
behavior = str(network.get("hardif_behavior", "native")).strip().lower()

allowed = {"native", "emulated_wifi"}
if behavior not in allowed:
    raise SystemExit(
        "[ERROR] network.hardif_behavior must be native or "
        f"emulated_wifi, got: {behavior!r}"
    )

print(f"{routing}\t{behavior}")
PY
)"

IFS=$'\t' read -r NETWORK_ROUTING HARDIF_BEHAVIOR <<< "$BATMAN_CONFIG"

if [[ "$NETWORK_ROUTING" == "batman" ]]; then
  BATADV_TOOLS="$ROOT_DIR/kernel/batman-adv-emulated-wifi"
  sudo "$BATADV_TOOLS/module-control.sh" ensure "$HARDIF_BEHAVIOR"
  python3 "$BATADV_TOOLS/module_status.py" \
    --requested-mode "$HARDIF_BEHAVIOR" \
    --require-match \
    > "$RESULT_DIR/batman_module.json"
fi

# -------------------------------
# Resolve binary from evaluation/apps.json
# -------------------------------
export ROOT_DIR
BIN="$(python - "$APP" <<'PY'
import json
import os
import sys
from pathlib import Path

if len(sys.argv) < 2:
    raise SystemExit("[ERROR] Missing app name argument to resolver")

app = sys.argv[1]
root = Path(os.environ["ROOT_DIR"])

apps_path = root / "evaluation" / "apps.json"
cfg = json.loads(apps_path.read_text(encoding="utf-8"))
apps = cfg.get("apps", {})

if app not in apps:
    raise SystemExit(f"[ERROR] App not found in apps.json: {app}")

bin_rel = apps[app].get("binary")
if not bin_rel:
    raise SystemExit(f"[ERROR] Missing 'binary' for app in apps.json: {app}")

bin_path = root / bin_rel
print(str(bin_path))
PY
)"

if [[ -z "$BIN" ]]; then
  echo "[ERROR] Could not resolve binary for app: $APP"
  exit 1
fi

# -------------------------------
# Generate mace.json (uses apps.json internally)
# -------------------------------
python "$ROOT_DIR/evaluation/generate_scenario.py" "$SCENARIO_DIR" "$APP"

# -------------------------------
# Generate node_config.json
# -------------------------------
python "$ROOT_DIR/evaluation/generate_node_config.py" \
  "$SCENARIO_SPEC" \
  "$NODE_CFG" \
  "$RESULT_DIR"

export CRDT_BIN="$BIN"
export CRDT_NODE_CONFIG="$NODE_CFG"

# -------------------------------
# Inject paths
# -------------------------------
sed -i \
  -e "s|__CRDT_BIN__|$BIN|g" \
  -e "s|__CRDT_NODE_CONFIG__|$NODE_CFG|g" \
  "$MACE_JSON"

cp "$SCENARIO_SPEC" "$RESULT_DIR/scenario.json"
cp "$MACE_JSON" "$RESULT_DIR/mace.json"
if [[ -d "$SCENARIO_DIR/mobility_traces" ]]; then
  rm -rf "$RESULT_DIR/mobility_traces"
  cp -r "$SCENARIO_DIR/mobility_traces" "$RESULT_DIR/mobility_traces"
fi

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
python "$ROOT_DIR/evaluation/process_pcaps.py" "$RESULT_DIR" "$APP" --append-netlog
