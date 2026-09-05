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
CLOCK_FILE="$RESULT_DIR/experiment_clock.json"

mkdir -p "$RESULT_DIR"

# -------------------------------
# Sanity check
# -------------------------------
[[ -f "$SCENARIO_SPEC" ]] || {
  echo "[ERROR] Missing scenario.json: $SCENARIO_SPEC"
  exit 1
}

WORKLOAD="$(
  python3 - "$SCENARIO_SPEC" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    scenario = json.load(stream)
node_config = scenario.get("node_config", {})
print(str(node_config.get("workload", scenario.get("workload", "gcounter"))).strip().lower())
PY
)"
if [[ "$WORKLOAD" != "gcounter" && "$WORKLOAD" != "spatial_coverage" ]]; then
  echo "[ERROR] workload must be gcounter or spatial_coverage: $WORKLOAD"
  exit 1
fi
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
"$ROOT_DIR/apps/crdt/build.sh" "$APP"

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

# -------------------------------
# Validate official traces and derive the external spatial run deadline
# -------------------------------
SPATIAL_CHECKPOINTS=""

if [[ "$WORKLOAD" == "spatial_coverage" ]]; then
  SPATIAL_CONFIG="$(
    python3 - "$SCENARIO_SPEC" "$NODE_CFG" <<'PY'
import json
import math
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    scenario = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    node_config = json.load(stream)

mobility = scenario.get("mobility", {})
if str(mobility.get("model", "none")).strip().lower() == "none":
    raise SystemExit(
        "[ERROR] spatial_coverage requires deterministic mobility traces"
    )
deterministic_replay = mobility.get("deterministic_replay", True)
if not isinstance(deterministic_replay, bool):
    raise SystemExit(
        "[ERROR] mobility.deterministic_replay must be boolean"
    )
if not deterministic_replay:
    raise SystemExit(
        "[ERROR] spatial_coverage requires mobility.deterministic_replay=true"
    )

coverage = scenario.get("coverage", scenario.get("spatial_coverage", {}))
if not isinstance(coverage, dict):
    raise SystemExit("[ERROR] coverage configuration must be an object")

def finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemExit(f"[ERROR] {name} must be a finite JSON number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise SystemExit(f"[ERROR] {name} must be a finite JSON number")
    if not math.isfinite(result):
        raise SystemExit(f"[ERROR] {name} must be a finite JSON number")
    return result

start_s = finite_number(coverage.get("start_delay_s", 30.0), "coverage.start_delay_s")
window_s = finite_number(coverage.get("post_coverage_window_s", 20.0), "coverage.post_coverage_window_s")
grace_s = finite_number(coverage.get("shutdown_grace_s", 3.0), "coverage.shutdown_grace_s")
checkpoints_explicit = "checkpoints" in coverage
raw_checkpoints = coverage.get("checkpoints", (0, 5, 10, 15, 20))
if isinstance(raw_checkpoints, str):
    try:
        checkpoints = tuple(
            float(item.strip())
            for item in raw_checkpoints.split(",")
            if item.strip()
        )
    except ValueError:
        raise SystemExit(
            "[ERROR] coverage.checkpoints must be numbers or a comma-separated string"
        )
else:
    try:
        checkpoints = tuple(
            finite_number(item, "coverage.checkpoints")
            for item in raw_checkpoints
        )
    except TypeError:
        raise SystemExit(
            "[ERROR] coverage.checkpoints must be numbers or a comma-separated string"
        )

if not math.isfinite(start_s) or start_s < 0.0:
    raise SystemExit("[ERROR] coverage.start_delay_s must be finite and >= 0")
if not math.isfinite(window_s) or window_s < 0.0:
    raise SystemExit("[ERROR] coverage.post_coverage_window_s must be finite and >= 0")
if not math.isfinite(grace_s) or grace_s <= 0.0:
    raise SystemExit("[ERROR] coverage.shutdown_grace_s must be finite and > 0")
if not checkpoints or any(not math.isfinite(value) or value < 0.0 for value in checkpoints):
    raise SystemExit("[ERROR] coverage.checkpoints must be non-empty, finite and >= 0")
if checkpoints_explicit and max(checkpoints) > window_s:
    raise SystemExit("[ERROR] coverage checkpoints must fit in post_coverage_window_s")
if not checkpoints_explicit:
    checkpoints = tuple(value for value in checkpoints if value <= window_s)
    if not any(math.isclose(value, window_s, rel_tol=0.0, abs_tol=1e-9) for value in checkpoints):
        checkpoints += (window_s,)

raw_require_motion = coverage.get("require_motion_after_cover", False)
if isinstance(raw_require_motion, bool):
    require_motion = raw_require_motion
elif isinstance(raw_require_motion, str) and raw_require_motion.strip().lower() in (
    "true", "false"
):
    require_motion = raw_require_motion.strip().lower() == "true"
else:
    raise SystemExit("[ERROR] coverage.require_motion_after_cover must be boolean")

grid = node_config.get("grid")
if not isinstance(grid, dict):
    raise SystemExit("[ERROR] generated spatial node config has no GridSpec")

def number(value):
    return format(float(value), ".17g")

print("\t".join((
    str(int(scenario["nodes"]["count"])),
    number(start_s),
    number(window_s),
    ",".join(number(value) for value in checkpoints),
    number(grace_s),
    "1" if require_motion else "0",
)))
PY
  )"

  IFS=$'\t' read -r \
    SPATIAL_NODE_COUNT \
    SPATIAL_START_SECONDS SPATIAL_WINDOW_SECONDS \
    SPATIAL_CHECKPOINTS SPATIAL_SHUTDOWN_GRACE_SECONDS \
    SPATIAL_REQUIRE_MOTION \
    <<< "$SPATIAL_CONFIG"

  mapfile -t TRACE_FILES < <(
    find "$SCENARIO_DIR/mobility_traces" -maxdepth 1 -type f \
      -name 'node_*.csv' -print 2>/dev/null | sort
  )
  if [[ "${#TRACE_FILES[@]}" -ne "$SPATIAL_NODE_COUNT" ]]; then
    echo "[ERROR] spatial_coverage requires one official trace per node; expected $SPATIAL_NODE_COUNT, found ${#TRACE_FILES[@]}"
    exit 1
  fi
  for ((node_index = 0; node_index < SPATIAL_NODE_COUNT; node_index++)); do
    if [[ ! -f "$SCENARIO_DIR/mobility_traces/node_${node_index}.csv" ]]; then
      echo "[ERROR] missing official trace for node $node_index"
      exit 1
    fi
  done

  TRACE_VALIDATION="$RESULT_DIR/trace_validation.json"
  TRACE_CHECK_ARGS=(
    --grid-config "$NODE_CFG"
    --coverage-start-time-s "$SPATIAL_START_SECONDS"
    --post-coverage-window-s "$SPATIAL_WINDOW_SECONDS"
  )
  if [[ "$SPATIAL_REQUIRE_MOTION" == "1" ]]; then
    TRACE_CHECK_ARGS+=(--require-motion-after-cover)
  fi
  python3 "$ROOT_DIR/evaluation/spatial_coverage.py" check-traces \
    "${TRACE_CHECK_ARGS[@]}" \
    --output "$TRACE_VALIDATION" \
    "${TRACE_FILES[@]}"

  SPATIAL_TIMING="$(
    python3 - \
      "$TRACE_VALIDATION" \
      "$SPATIAL_START_SECONDS" \
      "$SPATIAL_WINDOW_SECONDS" \
      "$SPATIAL_SHUTDOWN_GRACE_SECONDS" \
      "$SPATIAL_CHECKPOINTS" \
      "$RESULT_DIR/experiment_plan.json" <<'PY'
import json
import math
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
report = json.loads(report_path.read_text(encoding="utf-8"))
if not report.get("valid") or report.get("t_cover_s") is None:
    raise SystemExit("[ERROR] trace checker did not produce a valid T_cover")

start_s = float(sys.argv[2])
window_s = float(sys.argv[3])
grace_s = float(sys.argv[4])
checkpoints = tuple(float(item) for item in sys.argv[5].split(","))
t_cover_s = float(report["t_cover_s"])
experiment_end_s = t_cover_s + window_s
run_s = experiment_end_s - start_s
if not math.isfinite(run_s) or run_s <= 0.0:
    raise SystemExit("[ERROR] computed spatial application runtime is not positive")

# The helpers start immediately after the shared epoch waiter returns.  A
# small capture/logging margin keeps the final checkpoint observable while the
# Scenario deadline remains authoritative.  Keep it below the shutdown grace
# so the helpers can exit cleanly even when that grace is customized.
helper_margin_s = min(1.0, grace_s / 2.0)
capture_s = run_s + helper_margin_s
gps_s = run_s + helper_margin_s

plan = {
    "schema": "mace_spatial_experiment_plan_v1",
    "coverage_start_trace_s": start_s,
    "t_cover_trace_s": t_cover_s,
    "post_coverage_window_s": window_s,
    "experiment_end_trace_s": experiment_end_s,
    "application_runtime_s": run_s,
    "capture_runtime_s": capture_s,
    "gps_logger_runtime_s": gps_s,
    "shutdown_grace_s": grace_s,
    "checkpoint_offsets_s": list(checkpoints),
    "trace_validation": report_path.name,
}
Path(sys.argv[6]).write_text(
    json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)

def number(value):
    return format(float(value), ".9f")

print("\t".join((
    number(run_s),
    number(capture_s),
    number(gps_s),
    number(experiment_end_s),
)))
PY
  )"
  IFS=$'\t' read -r \
    SPATIAL_RUN_SECONDS SPATIAL_CAPTURE_SECONDS \
    SPATIAL_GPS_SECONDS SPATIAL_END_TRACE_SECONDS \
    <<< "$SPATIAL_TIMING"
fi

export CRDT_BIN="$BIN"
export CRDT_NODE_CONFIG="$NODE_CFG"

# -------------------------------
# Inject paths
# -------------------------------
if [[ "$WORKLOAD" == "spatial_coverage" ]]; then
  sed -i \
    -e "s|__CRDT_BIN__|$BIN|g" \
    -e "s|__CRDT_NODE_CONFIG__|$NODE_CFG|g" \
    -e "s|__EXPERIMENT_CLOCK__|$CLOCK_FILE|g" \
    -e "s|__SPATIAL_RUN_SECONDS__|$SPATIAL_RUN_SECONDS|g" \
    -e "s|__SPATIAL_CAPTURE_SECONDS__|$SPATIAL_CAPTURE_SECONDS|g" \
    -e "s|__SPATIAL_GPS_SECONDS__|$SPATIAL_GPS_SECONDS|g" \
    -e "s|__SPATIAL_END_TRACE_SECONDS__|$SPATIAL_END_TRACE_SECONDS|g" \
    "$MACE_JSON"
  if grep -q '__SPATIAL_' "$MACE_JSON"; then
    echo "[ERROR] unresolved spatial timing token in $MACE_JSON"
    exit 1
  fi
else
  sed -i \
    -e "s|__CRDT_BIN__|$BIN|g" \
    -e "s|__CRDT_NODE_CONFIG__|$NODE_CFG|g" \
    -e "s|__EXPERIMENT_CLOCK__|$CLOCK_FILE|g" \
    "$MACE_JSON"
fi

cp "$SCENARIO_SPEC" "$RESULT_DIR/scenario.json"
cp "$MACE_JSON" "$RESULT_DIR/mace.json"
if [[ -d "$SCENARIO_DIR/mobility_traces" ]]; then
  rm -rf "$RESULT_DIR/mobility_traces"
  cp -r "$SCENARIO_DIR/mobility_traces" "$RESULT_DIR/mobility_traces"
fi

# -------------------------------
# Run
# -------------------------------
PYMACE_RC=0
if [[ "$WORKLOAD" == "spatial_coverage" ]]; then
  # A reused RUN_ID must never let node waiters consume the previous run's
  # clock and start before the new replay epoch is published.  Delay removal
  # until all host-side preflight/build steps have succeeded.
  rm -f -- "$CLOCK_FILE"
  sudo "$ROOT_DIR/pymace.py" -s "$MACE_JSON" || PYMACE_RC=$?
else
  # Preserve the historical best-effort behavior for GCounter experiments.
  sudo "$ROOT_DIR/pymace.py" -s "$MACE_JSON" || true
fi

# -------------------------------
# Collect logs
# -------------------------------
python "$ROOT_DIR/evaluation/collect_logs.py" "$RESULT_DIR"

# -------------------------------
# Spatial CAR analysis (host-side, aligned by experiment_clock.json)
# -------------------------------
if [[ "$WORKLOAD" == "spatial_coverage" ]]; then
  python3 "$ROOT_DIR/evaluation/spatial_coverage.py" analyze \
    --run-dir "$RESULT_DIR" \
    --grid-config "$NODE_CFG" \
    --experiment-clock auto \
    --event-time-base unix \
    --checkpoints "$SPATIAL_CHECKPOINTS" \
    --output "$RESULT_DIR/spatial_coverage_analysis.json"
fi

# -------------------------------
# Post-process pcaps (host-side) and purge
# -------------------------------
python "$ROOT_DIR/evaluation/process_pcaps.py" "$RESULT_DIR" "$APP" --append-netlog

if [[ "$WORKLOAD" == "spatial_coverage" && "$PYMACE_RC" -ne 0 ]]; then
  echo "[ERROR] PyMACE exited with status $PYMACE_RC"
  exit "$PYMACE_RC"
fi
