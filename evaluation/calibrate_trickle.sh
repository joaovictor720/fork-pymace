#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./evaluation/calibrate_trickle.sh [--runs N] [--status-only]

Options:
  --scenario NAME      Calibration scenario base name. Default: trickle_calibration_ip
  --app NAME           App name. Default: trickle
  --runs N             Expected runs per variant. Default: 10
  --status-only        Print progress and exit without running anything
  --only-variant NAME  Resume just one expanded variant directory name

Behavior:
  - Expands scenarios/<scenario>/scenario.json into scenarios/<scenario>__expanded/.
  - Reuses runs only when run_status.json is finished, analyzable, and has pcap_metrics.csv.
  - Removes and reruns incomplete/interrupted run directories.
  - Rewrites variant_status.json, summary.csv, and final calibration CSV/plot when possible.
EOF
}

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO="trickle_calibration_ip"
APP="trickle"
RUNS="10"
STATUS_ONLY=false
ONLY_VARIANT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SCENARIO="$2"
      shift 2
      ;;
    --app|--algorithm)
      APP="$2"
      shift 2
      ;;
    --runs)
      RUNS="$2"
      shift 2
      ;;
    --status-only)
      STATUS_ONLY=true
      shift
      ;;
    --only-variant)
      ONLY_VARIANT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
EXPANDED_DIR="$ROOT_DIR/scenarios/${SCENARIO}__expanded"
RESULTS_ROOT="$ROOT_DIR/results/${SCENARIO}__expanded"

if [[ ! -f "$SCENARIO_DIR/scenario.json" ]]; then
  echo "[ERROR] Missing scenario: $SCENARIO_DIR/scenario.json" >&2
  exit 1
fi

is_run_complete() {
  local run_dir="$1"
  local status_path="$run_dir/run_status.json"

  python3 - "$status_path" "$run_dir" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
run_dir = Path(sys.argv[2])

if not status_path.exists():
    print("false")
    raise SystemExit(0)

try:
    data = json.loads(status_path.read_text(encoding="utf-8"))
except Exception:
    print("false")
    raise SystemExit(0)

status = data.get("status")
finished = data.get("finished_at") is not None
analyzable = bool(data.get("analyzable", False))
pcap_metrics_exists = bool(data.get("artifacts", {}).get("pcap_metrics_exists", False))

if analyzable and finished and status != "running" and pcap_metrics_exists and (run_dir / "pcap_metrics.csv").exists():
    print("true")
else:
    print("false")
PY
}

copy_variant_metadata() {
  local variant_name="$1"
  local variant_results_dir="$2"
  local variant_scenario_dir="$EXPANDED_DIR/$variant_name"

  mkdir -p "$variant_results_dir"

  if [[ -f "$variant_scenario_dir/scenario.json" ]]; then
    cp "$variant_scenario_dir/scenario.json" "$variant_results_dir/scenario.json"
  fi
  if [[ -f "$variant_scenario_dir/variant_meta.json" ]]; then
    cp "$variant_scenario_dir/variant_meta.json" "$variant_results_dir/variant_meta.json"
  fi
}

write_variant_status() {
  local variant_ref="$1"
  local app="$2"
  local variant_results_dir="$3"
  shift 3

  python3 - "$variant_ref" "$app" "$variant_results_dir" "$@" <<'PY'
import json
import sys
from pathlib import Path

variant = sys.argv[1]
app = sys.argv[2]
variant_results_dir = Path(sys.argv[3])
expected_runs = sys.argv[4:]

status_counts = {}
runs = []
found_runs = []
missing_status_files = []
complete_runs = 0
analyzable_runs = 0

for run_id in expected_runs:
    run_dir = variant_results_dir / run_id
    status_path = run_dir / "run_status.json"
    if not status_path.exists():
        missing_status_files.append(run_id)
        continue

    found_runs.append(run_id)

    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        missing_status_files.append(run_id)
        continue

    status = data.get("status", "unknown")
    analyzable = bool(data.get("analyzable", False))
    finished = data.get("finished_at") is not None
    pcap_metrics_exists = bool(data.get("artifacts", {}).get("pcap_metrics_exists", False))
    complete = (
        analyzable
        and finished
        and status != "running"
        and pcap_metrics_exists
        and (run_dir / "pcap_metrics.csv").exists()
    )

    status_counts[status] = status_counts.get(status, 0) + 1
    analyzable_runs += 1 if analyzable else 0
    complete_runs += 1 if complete else 0

    runs.append(
        {
            "run_id": run_id,
            "status": status,
            "analyzable": analyzable,
            "complete": complete,
            "finished_at": data.get("finished_at"),
            "pymace_rc": data.get("pymace", {}).get("rc"),
        }
    )

payload = {
    "variant": variant,
    "app": app,
    "expected_runs": expected_runs,
    "found_runs": found_runs,
    "missing_status_files": missing_status_files,
    "status_counts": status_counts,
    "analyzable_runs": analyzable_runs,
    "complete_runs": complete_runs,
    "all_analyzable": complete_runs == len(expected_runs),
    "all_complete": complete_runs == len(expected_runs),
    "runs": runs,
}

variant_results_dir.mkdir(parents=True, exist_ok=True)
(variant_results_dir / "variant_status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("true" if payload["all_complete"] else "false")
PY
}

print_status() {
  python3 - "$RESULTS_ROOT" "$EXPANDED_DIR" "$APP" "$RUNS" "$ONLY_VARIANT" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

results_root = Path(sys.argv[1])
expanded_dir = Path(sys.argv[2])
app = sys.argv[3]
runs = int(sys.argv[4])
only_variant = sys.argv[5]

variants = sorted(p.name for p in expanded_dir.iterdir() if p.is_dir())
if only_variant:
    variants = [v for v in variants if v == only_variant]

total_expected = len(variants) * runs
total_complete = 0
total_found = 0

print(f"scenario_variants={len(variants)} runs_per_variant={runs} expected_runs={total_expected}")

for variant in variants:
    app_dir = results_root / variant / app
    complete = 0
    found = 0
    statuses = {}

    for i in range(1, runs + 1):
        run_id = f"run_{i:03d}"
        status_path = app_dir / run_id / "run_status.json"
        if not status_path.exists():
            continue
        found += 1
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            statuses["unreadable"] = statuses.get("unreadable", 0) + 1
            continue
        status = data.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        is_complete = (
            bool(data.get("analyzable", False))
            and data.get("finished_at") is not None
            and status != "running"
            and bool(data.get("artifacts", {}).get("pcap_metrics_exists", False))
            and (app_dir / run_id / "pcap_metrics.csv").exists()
        )
        complete += 1 if is_complete else 0

    total_found += found
    total_complete += complete
    summary = "yes" if (app_dir / "summary.csv").exists() else "no"
    print(f"{variant}: complete={complete}/{runs} found={found}/{runs} summary={summary} statuses={statuses}")

print(f"TOTAL complete={total_complete}/{total_expected} found={total_found}/{total_expected}")

ps = subprocess.run(["ps", "-eo", "args"], text=True, capture_output=True, check=False)
stale = [line for line in ps.stdout.splitlines() if "vnoded -v -c /tmp/pycore" in line]
if stale:
    print(f"WARN stale_core_vnoded={len(stale)}; the next sudo pymace run normally starts with core-cleanup.")
PY
}

all_selected_variants_complete() {
  python3 - "$RESULTS_ROOT" "$EXPANDED_DIR" "$APP" "$RUNS" "$ONLY_VARIANT" <<'PY'
import json
import sys
from pathlib import Path

results_root = Path(sys.argv[1])
expanded_dir = Path(sys.argv[2])
app = sys.argv[3]
runs = int(sys.argv[4])
only_variant = sys.argv[5]

variants = sorted(p.name for p in expanded_dir.iterdir() if p.is_dir())
if only_variant:
    variants = [v for v in variants if v == only_variant]

for variant in variants:
    app_dir = results_root / variant / app
    for i in range(1, runs + 1):
        run_id = f"run_{i:03d}"
        run_dir = app_dir / run_id
        status_path = run_dir / "run_status.json"
        if not status_path.exists():
            print("false")
            raise SystemExit(0)
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            print("false")
            raise SystemExit(0)
        complete = (
            bool(data.get("analyzable", False))
            and data.get("finished_at") is not None
            and data.get("status") != "running"
            and bool(data.get("artifacts", {}).get("pcap_metrics_exists", False))
            and (run_dir / "pcap_metrics.csv").exists()
        )
        if not complete:
            print("false")
            raise SystemExit(0)

print("true")
PY
}

mapfile -t VARIANT_REFS < <(python3 "$ROOT_DIR/evaluation/expand_experiments.py" "$SCENARIO")

if [[ ${#VARIANT_REFS[@]} -eq 0 ]]; then
  echo "[ERROR] No variants returned for scenario: $SCENARIO" >&2
  exit 1
fi

if [[ "$STATUS_ONLY" == "true" ]]; then
  print_status
  exit 0
fi

EXPECTED_RUN_IDS=()
for RUN in $(seq 1 "$RUNS"); do
  EXPECTED_RUN_IDS+=("$(printf "run_%03d" "$RUN")")
done

echo "=== Trickle calibration resume ==="
echo "Scenario : $SCENARIO"
echo "App      : $APP"
echo "Runs     : $RUNS"
echo "Results  : $RESULTS_ROOT"
echo "=================================="

for VARIANT_REF in "${VARIANT_REFS[@]}"; do
  VARIANT_NAME="${VARIANT_REF#${SCENARIO}__expanded/}"

  if [[ -n "$ONLY_VARIANT" && "$VARIANT_NAME" != "$ONLY_VARIANT" ]]; then
    continue
  fi

  VARIANT_RESULTS_DIR="$RESULTS_ROOT/$VARIANT_NAME/$APP"
  copy_variant_metadata "$VARIANT_NAME" "$VARIANT_RESULTS_DIR"

  echo
  echo "=== Variant: $VARIANT_NAME ==="

  for RUN_ID in "${EXPECTED_RUN_IDS[@]}"; do
    RUN_DIR="$VARIANT_RESULTS_DIR/$RUN_ID"

    if [[ "$(is_run_complete "$RUN_DIR")" == "true" ]]; then
      echo "[SKIP] $RUN_ID already complete"
      continue
    fi

    if [[ -d "$RUN_DIR" ]]; then
      echo "[RERUN] $RUN_ID incomplete/interrupted; replacing $RUN_DIR"
      rm -rf "$RUN_DIR"
    else
      echo "[RUN] $RUN_ID missing"
    fi

    "$ROOT_DIR/evaluation/run_scenario.sh" "$VARIANT_REF" "$APP" "$RUN_ID" --keep-going
  done

  ALL_COMPLETE="$(write_variant_status "$VARIANT_REF" "$APP" "$VARIANT_RESULTS_DIR" "${EXPECTED_RUN_IDS[@]}")"

  if [[ "$ALL_COMPLETE" == "true" ]]; then
    python3 "$ROOT_DIR/evaluation/parse_metrics.py" "$VARIANT_RESULTS_DIR"
  else
    echo "[WARN] Variant still incomplete; summary.csv not refreshed: $VARIANT_NAME"
  fi
done

print_status

if [[ "$(all_selected_variants_complete)" == "true" ]]; then
  if python3 "$ROOT_DIR/evaluation/analyze_trickle_calibration.py" "$RESULTS_ROOT" --app "$APP"; then
    echo "[OK] Calibration analysis refreshed."
  else
    echo "[WARN] Calibration analysis failed after runs completed."
  fi
else
  echo "[WARN] Calibration analysis not refreshed; finish missing variants first."
fi
