#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="$ROOT_DIR/evaluation/run_experiment.sh"
JOBS_FILE="$ROOT_DIR/evaluation/jobs.json"

OVERRIDE_RUNS=""
ONLY_SCENARIO=""
ONLY_APP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs) OVERRIDE_RUNS="$2"; shift 2 ;;
    --only-scenario) ONLY_SCENARIO="$2"; shift 2 ;;
    --only-app) ONLY_APP="$2"; shift 2 ;;
    --jobs) JOBS_FILE="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--runs N] [--only-scenario NAME] [--only-app APP] [--jobs FILE]"
      exit 1
      ;;
  esac
done

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "[ERROR] jobs file not found: $JOBS_FILE"
  exit 1
fi

if [[ ! -x "$RUN_SCRIPT" ]]; then
  echo "[ERROR] run_experiment.sh not found or not executable: $RUN_SCRIPT"
  exit 1
fi

# -----------------------------------------------------------------------------
# Carrega jobs via Python e imprime linhas: "scenario<TAB>app<TAB>runs"
# -----------------------------------------------------------------------------
JOBS_TSV="$(
python3 - "$JOBS_FILE" "$OVERRIDE_RUNS" <<'PY'
import json
import sys

path = sys.argv[1]
override_runs = sys.argv[2].strip() if len(sys.argv) > 2 else ""
override_runs = override_runs if override_runs else None

with open(path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

default_runs = cfg.get("default_runs", 5)
jobs = cfg.get("jobs", [])
if not isinstance(jobs, list) or not jobs:
    raise SystemExit("[ERROR] jobs.json: 'jobs' must be a non-empty list")

def to_int(x, what):
    try:
        v = int(x)
    except Exception:
        raise SystemExit(f"[ERROR] jobs.json: invalid {what}={x!r}")
    if v <= 0:
        raise SystemExit(f"[ERROR] jobs.json: {what} must be > 0 (got {v})")
    return v

default_runs = to_int(default_runs, "default_runs")

if override_runs is not None:
    default_runs = to_int(override_runs, "--runs")

for idx, job in enumerate(jobs):
    if not isinstance(job, dict):
        raise SystemExit(f"[ERROR] jobs.json: job[{idx}] must be an object")

    scenario = job.get("scenario")
    app = job.get("app")
    if not scenario or not isinstance(scenario, str):
        raise SystemExit(f"[ERROR] jobs.json: job[{idx}] missing/invalid 'scenario'")
    if not app or not isinstance(app, str):
        raise SystemExit(f"[ERROR] jobs.json: job[{idx}] missing/invalid 'app'")

    runs = job.get("runs", default_runs)
    runs = to_int(runs, f"job[{idx}].runs")

    print(f"{scenario}\t{app}\t{runs}")
PY
)"

echo "=================================================="
echo "RUN_ALL - Bateria de experimentos"
echo "Root        : $ROOT_DIR"
echo "Jobs file   : $JOBS_FILE"
[[ -n "$OVERRIDE_RUNS" ]] && echo "Runs override: $OVERRIDE_RUNS"
[[ -n "$ONLY_SCENARIO" ]] && echo "OnlyScenario: $ONLY_SCENARIO"
[[ -n "$ONLY_APP" ]] && echo "OnlyApp     : $ONLY_APP"
echo "=================================================="
echo ""

run_job () {
  local scenario="$1"
  local app="$2"
  local runs="$3"

  if [[ -n "$ONLY_SCENARIO" && "$scenario" != "$ONLY_SCENARIO" ]]; then
    return 0
  fi
  if [[ -n "$ONLY_APP" && "$app" != "$ONLY_APP" ]]; then
    return 0
  fi

  local scenario_json="$ROOT_DIR/scenarios/$scenario/scenario.json"
  if [[ ! -f "$scenario_json" ]]; then
    echo "[ERROR] scenario.json not found: $scenario_json"
    exit 1
  fi

  echo "##################################################"
  echo "Scenario : $scenario"
  echo "App      : $app"
  echo "Runs     : $runs"
  echo "##################################################"

  "$RUN_SCRIPT" --scenario "$scenario" --app "$app" --runs "$runs"

  echo ">>> Done: scenario=$scenario app=$app"
  echo ""
}

# -----------------------------------------------------------------------------
# Executa jobs em ordem
# -----------------------------------------------------------------------------
while IFS=$'\t' read -r scenario app runs; do
  [[ -z "${scenario:-}" ]] && continue
  run_job "$scenario" "$app" "$runs"
done <<< "$JOBS_TSV"

echo "=================================================="
echo "RUN_ALL - Finalizado"
echo "Resultados em: $ROOT_DIR/results/"
echo "=================================================="
