#!/bin/bash
set -uo pipefail

usage() {
  echo "Usage: $0 <scenario> <app> <run_id> [--keep-going]"
}

KEEP_GOING=false
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-going)
      KEEP_GOING=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

SCENARIO="${1:-}"
APP="${2:-}"
RUN_ID="${3:-}"

if [[ -z "$SCENARIO" || -z "$APP" || -z "$RUN_ID" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_CWD="$(pwd -P)"

SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
SCENARIO_SPEC="$SCENARIO_DIR/scenario.json"

RESULT_DIR="$ROOT_DIR/results/$SCENARIO/$APP/$RUN_ID"
NODE_CFG="$RESULT_DIR/node_config.json"
MACE_JSON="$RESULT_DIR/mace.json"
RUN_STATUS="$RESULT_DIR/run_status.json"

PYMACE_STDOUT="pymace.stdout.log"
PYMACE_STDERR="pymace.stderr.log"
PYMACE_STDOUT_PATH="$RESULT_DIR/$PYMACE_STDOUT"
PYMACE_STDERR_PATH="$RESULT_DIR/$PYMACE_STDERR"

COLLECT_SUMMARY_PATH="$RESULT_DIR/collect_logs_summary.json"
PROCESS_SUMMARY_PATH="$RESULT_DIR/process_pcaps_summary.json"

mkdir -p "$RESULT_DIR"
: > "$PYMACE_STDOUT_PATH"
: > "$PYMACE_STDERR_PATH"

STARTED_AT="$(date --iso-8601=seconds)"
FINISHED_AT=""
EXPECTED_NODE_COUNT=""
BIN=""
PYMACE_RC=""
COLLECT_LOGS_RC=""
PROCESS_PCAPS_RC=""
STAGE="setup"
SETUP_OK=true

read_summary_json_field() {
  local path="$1"
  local field="$2"
  python3 - "$path" "$field" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
field = sys.argv[2]

if not path.exists():
    print("")
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)

value = data
for part in field.split("."):
    if isinstance(value, dict) and part in value:
        value = value[part]
    else:
        print("")
        raise SystemExit(0)

if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

update_run_status() {
  export RUN_STATUS RESULT_DIR ROOT_DIR SCRIPT_CWD SCENARIO APP RUN_ID STARTED_AT FINISHED_AT
  export KEEP_GOING STAGE SETUP_OK EXPECTED_NODE_COUNT BIN PYMACE_RC COLLECT_LOGS_RC PROCESS_PCAPS_RC
  export PYMACE_STDOUT PYMACE_STDERR COLLECT_SUMMARY_PATH PROCESS_SUMMARY_PATH NODE_CFG MACE_JSON

  python3 - <<'PY'
import json
import os
from pathlib import Path


def env_bool(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def env_int(name: str):
    raw = os.environ.get(name, "")
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


result_dir = Path(os.environ["RESULT_DIR"])
run_status_path = Path(os.environ["RUN_STATUS"])
collect_summary_path = Path(os.environ["COLLECT_SUMMARY_PATH"])
process_summary_path = Path(os.environ["PROCESS_SUMMARY_PATH"])
node_cfg_path = Path(os.environ["NODE_CFG"])

collect_summary = load_json(collect_summary_path) or {}
process_summary = load_json(process_summary_path) or {}

node_logs_count = sum(1 for p in result_dir.glob("node_*.log") if p.is_file())
node_netlogs_count = sum(1 for p in result_dir.glob("node_*.net.log") if p.is_file())
pcap_count = sum(1 for p in result_dir.glob("node_*.pcap") if p.is_file())

nonempty_node_logs_count = sum(1 for p in result_dir.glob("node_*.log") if p.is_file() and p.stat().st_size > 0)
useful_netlogs_count = sum(1 for p in result_dir.glob("node_*.net.log") if p.is_file() and p.stat().st_size > 0)
nonempty_pcaps_count = sum(1 for p in result_dir.glob("node_*.pcap") if p.is_file() and p.stat().st_size > 0)

pcap_metrics_exists = (result_dir / "pcap_metrics.csv").exists()
pcap_metrics_jsonl_exists = (result_dir / "pcap_metrics.jsonl").exists()
node_config_exists = node_cfg_path.exists()

expected_node_count = env_int("EXPECTED_NODE_COUNT")
useful_pcaps = process_summary.get("useful_pcaps")
process_status = process_summary.get("status")

if useful_pcaps is None:
    useful_pcaps = nonempty_pcaps_count

logs_sufficient = nonempty_node_logs_count > 0
if expected_node_count and expected_node_count > 0:
    logs_sufficient = nonempty_node_logs_count >= expected_node_count

network_sufficient = False
if useful_pcaps and useful_pcaps > 0:
    network_sufficient = True
elif useful_netlogs_count > 0:
    network_sufficient = True
elif pcap_metrics_exists and process_status in (None, "success", "partial_success"):
    network_sufficient = True

analyzable = logs_sufficient and network_sufficient

setup_ok = env_bool("SETUP_OK")
pymace_rc = env_int("PYMACE_RC")
collect_logs_rc = env_int("COLLECT_LOGS_RC")
process_pcaps_rc = env_int("PROCESS_PCAPS_RC")

artifacts_present = any(
    (
        node_logs_count > 0,
        node_netlogs_count > 0,
        pcap_count > 0,
        pcap_metrics_exists,
        pcap_metrics_jsonl_exists,
    )
)

postprocessing_failed = False
if collect_logs_rc not in (None, 0):
    postprocessing_failed = True
if process_pcaps_rc not in (None, 0):
    if pcap_count > 0 and (not useful_pcaps or useful_pcaps <= 0):
        postprocessing_failed = True

stage = os.environ.get("STAGE", "setup")
status = "running"

if stage == "setup_failed":
    status = "setup_failed"
elif stage != "done":
    status = "running"
else:
    if not setup_ok:
        status = "setup_failed"
    elif analyzable and pymace_rc == 0 and not postprocessing_failed:
        status = "success"
    elif analyzable and pymace_rc not in (None, 0):
        status = "execution_nonzero_but_artifacts_present"
    elif postprocessing_failed and artifacts_present:
        status = "postprocessing_failed"
    else:
        status = "execution_failed_no_artifacts"

data = {
    "scenario": os.environ["SCENARIO"],
    "app": os.environ["APP"],
    "run_id": os.environ["RUN_ID"],
    "cwd": os.environ["SCRIPT_CWD"],
    "started_at": os.environ["STARTED_AT"],
    "finished_at": os.environ.get("FINISHED_AT") or None,
    "keep_going": env_bool("KEEP_GOING"),
    "expected_node_count": expected_node_count,
    "pymace": {
        "cmd": [
            "sudo",
            "-E",
            "python3",
            "-u",
            str(Path(os.environ["ROOT_DIR"]) / "pymace.py"),
            "-s",
            os.environ["MACE_JSON"],
        ],
        "rc": pymace_rc,
        "stdout_path": os.environ["PYMACE_STDOUT"],
        "stderr_path": os.environ["PYMACE_STDERR"],
    },
    "artifacts": {
        "node_logs_count": node_logs_count,
        "node_netlogs_count": node_netlogs_count,
        "pcap_count": pcap_count,
        "pcap_metrics_exists": pcap_metrics_exists,
        "pcap_metrics_jsonl_exists": pcap_metrics_jsonl_exists,
        "node_config_exists": node_config_exists,
        "nonempty_node_logs_count": nonempty_node_logs_count,
        "useful_netlogs_count": useful_netlogs_count,
        "useful_pcap_count": useful_pcaps,
    },
    "post": {
        "collect_logs_rc": collect_logs_rc,
        "collect_logs_summary_path": Path(os.environ["COLLECT_SUMMARY_PATH"]).name,
        "collect_logs_moved_count": collect_summary.get("moved_count"),
        "process_pcaps_rc": process_pcaps_rc,
        "process_pcaps_summary_path": Path(os.environ["PROCESS_SUMMARY_PATH"]).name,
        "process_pcaps_status": process_summary.get("status"),
        "process_pcaps_total_pcaps": process_summary.get("total_pcaps"),
        "process_pcaps_useful_pcaps": process_summary.get("useful_pcaps"),
    },
    "status": status,
    "analyzable": analyzable,
}

run_status_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

finish_and_exit() {
  FINISHED_AT="$(date --iso-8601=seconds)"
  STAGE="done"
  update_run_status

  local analyzable
  analyzable="$(read_summary_json_field "$RUN_STATUS" "analyzable")"
  if [[ "$KEEP_GOING" == "true" || "$analyzable" == "true" ]]; then
    exit 0
  fi
  exit 1
}

fail_setup() {
  SETUP_OK=false
  STAGE="setup_failed"
  FINISHED_AT="$(date --iso-8601=seconds)"
  update_run_status
  if [[ "$KEEP_GOING" == "true" ]]; then
    exit 0
  fi
  exit 1
}

update_run_status

if [[ ! -f "$SCENARIO_SPEC" ]]; then
  echo "[ERROR] Missing scenario.json: $SCENARIO_SPEC" >&2
  fail_setup
fi

EXPECTED_NODE_COUNT="$(python3 - "$SCENARIO_SPEC" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    scenario = json.load(f)

print(int(scenario["nodes"]["count"]))
PY
)" || EXPECTED_NODE_COUNT=""

export ROOT_DIR
BIN="$(python3 - "$APP" <<'PY'
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
)" || BIN=""

if [[ -z "$BIN" ]]; then
  echo "[ERROR] Could not resolve binary for app: $APP" >&2
  fail_setup
fi

if ! python3 "$ROOT_DIR/evaluation/generate_scenario.py" "$SCENARIO_DIR" "$APP" --out "$MACE_JSON"; then
  echo "[ERROR] Failed to generate scenario for $SCENARIO / $APP" >&2
  fail_setup
fi

if ! python3 "$ROOT_DIR/evaluation/generate_node_config.py" "$SCENARIO_SPEC" "$NODE_CFG" "$RESULT_DIR"; then
  echo "[ERROR] Failed to generate node_config.json" >&2
  fail_setup
fi

export CRDT_BIN="$BIN"
export CRDT_NODE_CONFIG="$NODE_CFG"

if ! python3 - "$MACE_JSON" "$BIN" "$NODE_CFG" <<'PY'
from pathlib import Path
import sys

mace_json = Path(sys.argv[1])
bin_path = sys.argv[2]
node_cfg = sys.argv[3]

content = mace_json.read_text(encoding="utf-8")
content = content.replace("__CRDT_BIN__", bin_path)
content = content.replace("__CRDT_NODE_CONFIG__", node_cfg)
mace_json.write_text(content, encoding="utf-8")
PY
then
  echo "[ERROR] Failed to inject runtime paths into $MACE_JSON" >&2
  fail_setup
fi

STAGE="execution"
update_run_status

if sudo -E python3 -u "$ROOT_DIR/pymace.py" -s "$MACE_JSON" >"$PYMACE_STDOUT_PATH" 2>"$PYMACE_STDERR_PATH"; then
  PYMACE_RC=0
else
  PYMACE_RC=$?
fi

STAGE="post_collect"
update_run_status

if python3 "$ROOT_DIR/evaluation/collect_logs.py" "$RESULT_DIR"; then
  COLLECT_LOGS_RC=0
else
  COLLECT_LOGS_RC=$?
fi

STAGE="post_process_pcaps"
update_run_status

if python3 "$ROOT_DIR/evaluation/process_pcaps.py" "$RESULT_DIR" "$APP" --append-netlog; then
  PROCESS_PCAPS_RC=0
else
  PROCESS_PCAPS_RC=$?
fi

python3 "$ROOT_DIR/evaluation/parse_message_counts.py" "$RESULT_DIR" --write-run-files >/dev/null || true

finish_and_exit
