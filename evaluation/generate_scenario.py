import json
import sys
import random
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mobility_trace import (
    TRACE_POLICY,
    deterministic_trace_enabled,
    generate_traces_for_nodes,
    trace_duration_s,
    trace_interval_s,
)
from seed_utils import central_seed, derive_seed

APPLICATION_START_DELAY = 30


def finite_json_number(value: Any, label: str) -> float:
    """Parse a finite JSON number without treating bool/string as numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite JSON number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be a finite JSON number")
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite JSON number")
    return result


def generate_random_positions(n: int, area: Dict[str, float], rng: random.Random) -> List[Tuple[float, float]]:
    return [(rng.uniform(0, area["x"]), rng.uniform(0, area["y"])) for _ in range(n)]

def generate_grid_positions(n: int, area: Dict[str, float]) -> List[Tuple[float, float]]:
    side = math.ceil(math.sqrt(n))
    dx = area["x"] / side
    dy = area["y"] / side
    positions: List[Tuple[float, float]] = []
    idx = 0
    for i in range(side):
        for j in range(side):
            if idx >= n:
                break
            positions.append(((i + 0.5) * dx, (j + 0.5) * dy))
            idx += 1
    return positions

if len(sys.argv) != 3:
    print("Usage: generate_scenario.py <scenario_dir> <app>")
    sys.exit(1)

scenario_dir = Path(sys.argv[1])
app = sys.argv[2]

root = Path(__file__).resolve().parent.parent
apps_path = root / "evaluation" / "apps.json"
apps_cfg = json.loads(apps_path.read_text(encoding="utf-8"))
apps = apps_cfg.get("apps", {})
if app not in apps:
    raise SystemExit(f"[ERROR] App not found in apps.json: {app}")
app_cfg = apps[app]

scenario_file = scenario_dir / "scenario.json"
out_file = scenario_dir / "mace.json"

if not scenario_file.exists():
    raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

with open(scenario_file, "r", encoding="utf-8") as f:
    sc = json.load(f)

seed = central_seed(sc)
node_position_seed = derive_seed(seed, "node_positions", avoid_zero=False)
position_rng = random.Random(node_position_seed)

node_count = sc["nodes"]["count"]
distribution = sc["nodes"].get("distribution", "random")
raw_area = sc["simulation"]["area"]

if isinstance(raw_area, (list, tuple)):
    area = {"x": float(raw_area[0]), "y": float(raw_area[1])}
else:
    area = {"x": float(raw_area["x"]), "y": float(raw_area["y"])}

if distribution == "random":
    positions = generate_random_positions(node_count, area, position_rng)
elif distribution == "grid":
    positions = generate_grid_positions(node_count, area)
else:
    raise ValueError(f"Unknown node distribution: {distribution}")

nodes: List[Dict[str, Any]] = []
net_setup = str(app_cfg.get("net_setup", "ip")).lower()
tcpdump_filter = str(app_cfg.get("tcpdump_filter", "")).strip()

node_cfg = sc.get("node_config", {})
workload = str(
    node_cfg.get("workload", sc.get("workload", "gcounter"))
).strip().lower()
if workload not in ("gcounter", "spatial_coverage"):
    raise ValueError("workload must be gcounter or spatial_coverage")
spatial_coverage_enabled = workload == "spatial_coverage"
coverage_start_delay_s = float(APPLICATION_START_DELAY)
post_coverage_window_s = 20.0
shutdown_grace_s = 3.0
if spatial_coverage_enabled:
    coverage_cfg = sc.get("coverage", sc.get("spatial_coverage", {}))
    if not isinstance(coverage_cfg, dict):
        raise ValueError("coverage configuration must be an object")
    coverage_start_delay_s = finite_json_number(
        coverage_cfg.get("start_delay_s", APPLICATION_START_DELAY),
        "coverage.start_delay_s",
    )
    post_coverage_window_s = finite_json_number(
        coverage_cfg.get("post_coverage_window_s", 20.0),
        "coverage.post_coverage_window_s",
    )
    shutdown_grace_s = finite_json_number(
        coverage_cfg.get("shutdown_grace_s", 3.0),
        "coverage.shutdown_grace_s",
    )
    if coverage_start_delay_s < 0.0:
        raise ValueError("coverage.start_delay_s must be finite and >= 0")
    if post_coverage_window_s < 0.0:
        raise ValueError(
            "coverage.post_coverage_window_s must be finite and >= 0"
        )
    if shutdown_grace_s <= 0.0:
        raise ValueError("coverage.shutdown_grace_s must be finite and > 0")
ip_iface = "eth0"
if net_setup != "batman":
    ip_iface = str(
        node_cfg.get("usfd_interface", node_cfg.get("interface", "eth0"))
    ).strip() or "eth0"
if spatial_coverage_enabled:
    # Spatial applications are externally bounded by the experiment runner;
    # duration/cooldown are deliberately not passed to the application.  The
    # runner replaces these tokens after its trace checker computes T_cover.
    simulation_duration_s = finite_json_number(
        sc["simulation"]["duration"], "simulation.duration"
    )
    if (
        not math.isfinite(simulation_duration_s)
        or simulation_duration_s <= coverage_start_delay_s
    ):
        raise ValueError(
            "simulation.duration must exceed coverage.start_delay_s"
        )
    SPATIAL_RUN_SEC = "__SPATIAL_RUN_SECONDS__"
    CAPTURE_SEC = "__SPATIAL_CAPTURE_SECONDS__"
else:
    duration_s = float(node_cfg.get("duration", 10))
    cooldown_s = float(node_cfg.get("cooldown", 10))
    SPATIAL_RUN_SEC = 0.0
    # Legacy GCounter capture covers duration+cooldown with a small margin.
    CAPTURE_SEC = int(math.ceil(duration_s + cooldown_s + 1.0))

# GPS logging: conforme pedido do professor
if spatial_coverage_enabled:
    raw_gps_interval = node_cfg.get(
        "gps_interval",
        finite_json_number(
            node_cfg.get("position_poll_interval_ms", 100),
            "node_config.position_poll_interval_ms",
        ) / 1000.0,
    )
    GPS_INTERVAL_S = finite_json_number(
        raw_gps_interval, "node_config.gps_interval"
    )
else:
    GPS_INTERVAL_S = float(node_cfg.get("gps_interval", 0.5))
GPS_LOG_SEC = (
    "__SPATIAL_GPS_SECONDS__"
    if spatial_coverage_enabled
    else float(node_cfg.get("gps_duration", duration_s + cooldown_s))
)
if not math.isfinite(GPS_INTERVAL_S) or GPS_INTERVAL_S <= 0.0 or (
    not spatial_coverage_enabled
    and (not math.isfinite(GPS_LOG_SEC) or GPS_LOG_SEC <= 0.0)
):
    raise ValueError("GPS interval and duration must be positive")

gps_logger_path = root / "evaluation" / "gps_logger.py"
clock_waiter_path = root / "evaluation" / "wait_for_experiment_clock.py"
mob = sc["mobility"]

if spatial_coverage_enabled:
    deterministic_replay = mob.get("deterministic_replay", True)
    if not isinstance(deterministic_replay, bool):
        raise ValueError("mobility.deterministic_replay must be boolean")
    if not deterministic_replay:
        raise ValueError(
            "spatial_coverage requires mobility.deterministic_replay=true"
        )

if "speed" in mob:
    default_vmin, default_vmax = mob["speed"]
else:
    default_vmin = mob.get("speed_min", 0)
    default_vmax = mob.get("speed_max", 0)

node_velocities = [
    (float(default_vmin), float(default_vmax))
    for _ in range(node_count)
]
mobility_seeds = [
    derive_seed(seed, "mobility", i)
    for i in range(node_count)
]

trace_manifest = None
trace_enabled = (
    str(mob.get("model", "none")).strip().lower() != "none"
    and deterministic_trace_enabled(mob)
)
if trace_enabled:
    trace_dir = scenario_dir / "mobility_traces"
    trace_interval = trace_interval_s(sc)
    trace_duration = trace_duration_s(sc)
    trace_manifest = generate_traces_for_nodes(
        trace_dir,
        central_seed=seed,
        mobility_config=mob,
        dimensions=(area["x"], area["y"]),
        velocities=node_velocities,
        mobility_seeds=mobility_seeds,
        interval_s=trace_interval,
        duration_s=trace_duration,
    )
else:
    trace_dir = None
    trace_interval = None
    trace_duration = None

for i, (x, y) in enumerate(positions):
    vmin, vmax = node_velocities[i]

    mobility = {
        "model": mob["model"],
        "zone_x": area["x"],
        "zone_y": area["y"],
        "zone_z": 0,
        "velocity_lower": vmin,
        "velocity_upper": vmax,
        "pause": mob.get("pause", 0),
        "seed": mobility_seeds[i],
    }

    if trace_manifest is not None and trace_dir is not None:
        node_trace = trace_manifest["nodes"][i]
        trace_file = (trace_dir / node_trace["file"]).resolve()
        mobility.update({
            "deterministic_replay": True,
            "trace_policy": TRACE_POLICY,
            "source_model": mob["model"],
            "trace_file": str(trace_file),
            "trace_sha256": node_trace["sha256"],
            "trace_interval": trace_interval,
            "trace_duration": trace_duration,
        })

    if net_setup == "batman":
        base_net_setup = (
            f"sudo ip addr flush dev eth0; "
            f"sudo ip link set up dev eth0; "
            f"sudo batctl if add eth0; "
            f"sudo ip link set up dev bat0; "
            f"sudo ip addr add 10.0.0.{i+1}/24 dev bat0; "
        )
    else:
        base_net_setup = (
            f"sudo ip link set up dev {ip_iface}; "
        )

    if spatial_coverage_enabled:
        startup_sequence = (
            f"{base_net_setup}"
            f"/usr/bin/python3 {clock_waiter_path} "
            f"--clock __EXPERIMENT_CLOCK__; "
        )
        application_command = (
            f"timeout --signal=TERM --kill-after=2 {SPATIAL_RUN_SEC} "
            f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__; "
        )
    else:
        startup_sequence = f"sleep {APPLICATION_START_DELAY}; {base_net_setup}"
        application_command = (
            f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__; "
        )

    function = [
        f"/bin/bash -lc \""
        f"ulimit -c 0; "
        f"set -x; "
        f"{startup_sequence}"
        f"RESULT_DIR=\\$(grep '\\\"log_dir\\\"' __CRDT_NODE_CONFIG__ | "
        f"sed -E 's/.*\\\"log_dir\\\"[[:space:]]*:[[:space:]]*\\\"([^\\\"]+)\\\".*/\\1/'); "
        f"LOG_FILE=\\\"\\$RESULT_DIR/node_{i}.net.log\\\"; "
        f"PCAP_FILE=\\\"\\$RESULT_DIR/node_{i}.pcap\\\"; "
        f"TCPDUMP_ERR=\\\"\\$RESULT_DIR/node_{i}.tcpdump.stderr\\\"; "
        f"GPS_FILE=\\\"\\$RESULT_DIR/node_{i}.gps.csv\\\"; "
        f"GPS_ERR=\\\"\\$RESULT_DIR/node_{i}.gps.stderr\\\"; "
        f"echo \\\"APP={app}\\\" > \\\"\\$LOG_FILE\\\"; "

        # tcpdump com timeout
        f"sudo timeout -s INT {CAPTURE_SEC} tcpdump -i {ip_iface} -w \\\"\\$PCAP_FILE\\\" "
        f"'{tcpdump_filter}' >/dev/null 2>\\\"\\$TCPDUMP_ERR\\\" & "
        f"TCPDUMP_PID=\\$!; "
        f"echo \\\"TCPDUMP_PID=\\$TCPDUMP_PID\\\" >> \\\"\\$LOG_FILE\\\"; "

        # GPS logger (background)
        f"GPS_TAG=\\\"node{i}\\\"; "
        f"/usr/bin/python3 {gps_logger_path} "
        f"--tag \\\"\\$GPS_TAG\\\" --node {i} --out \\\"\\$GPS_FILE\\\" "
        f"--interval {GPS_INTERVAL_S} --duration {GPS_LOG_SEC} "
        f">/dev/null 2>\\\"\\$GPS_ERR\\\" & "
        f"GPS_PID=\\$!; "
        f"echo \\\"GPS_PID=\\$GPS_PID\\\" >> \\\"\\$LOG_FILE\\\"; "
        f"echo \\\"GPS_FILE=\\$GPS_FILE\\\" >> \\\"\\$LOG_FILE\\\"; "

        # App
        f"{application_command}"
        f"APP_RC=\\$?; "
        f"echo \\\"APP_RC=\\$APP_RC\\\" >> \\\"\\$LOG_FILE\\\"; "

        # waits
        f"wait \\$TCPDUMP_PID 2>/dev/null || true; "
        f"wait \\$GPS_PID 2>/dev/null || true; "
        f"sync; "
        f"echo \\\"PCAP_SAVED=\\$PCAP_FILE\\\" >> \\\"\\$LOG_FILE\\\"; "
        f"echo \\\"TCPDUMP_STDERR=\\$TCPDUMP_ERR\\\" >> \\\"\\$LOG_FILE\\\"; "
        f"echo \\\"GPS_STDERR=\\$GPS_ERR\\\" >> \\\"\\$LOG_FILE\\\"\""
    ]

    node = {
        "name": f"node{i}",
        "settings": {
            "_id": i,
            "x": round(x, 2),
            "y": round(y, 2),
            "type": "node",
            "range": sc["network"]["range"]
        },
        "type": "UTM",
        "function": function,
        "extra": {
            "disks": "False",
            "dump": {"start": "False", "delay": 0, "duration": 0},
            "network": ["mesh"],
            "mobility": mobility
        }
    }
    nodes.append(node)

mace = {
    "settings": {
        "core": "True",
        "omnet": "False",
        "dump": "False",
        "number_of_nodes": node_count,
        "start_delay": sc["simulation"]["start_delay"],
        "runtime": sc["simulation"]["duration"],
        "username": "mace",
        "disks_folder": "/mnt/pymace/",
        "report_folder": "/home/mace/git/fork-pymace/reports/",
        "emane_location": "/usr/share/emane",
        "emane_scale": 1.0
    },
    "networks": [
        {
            "name": "mesh",
            "prefix": "10.0.0.0/24",
            "routing": sc["network"]["routing"],
            "settings": {
                "range": str(sc["network"]["range"]),
                "bandwidth": str(sc["network"]["bandwidth"]),
                "delay": str(sc["network"]["delay"]),
                "jitter": str(sc["network"]["jitter"]),
                "error": str(sc["network"]["error"]),
                "emane": "False"
            }
        }
    ],
    "nodes": nodes
}

if spatial_coverage_enabled:
    mace["settings"]["experiment_clock_file"] = "__EXPERIMENT_CLOCK__"
    mace["settings"]["coverage_start_delay_s"] = coverage_start_delay_s
    mace["settings"]["experiment_end_trace_s"] = (
        "__SPATIAL_END_TRACE_SECONDS__"
    )
    mace["settings"]["shutdown_grace_s"] = shutdown_grace_s
    mace["settings"]["workload"] = "spatial_coverage"

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(mace, f, indent=2)

print(f"[OK] Generated {out_file}")
print(f"[INFO] Nodes: {node_count}, distribution: {distribution}, central_seed: {seed}, node_position_seed: {node_position_seed}, app: {app}, net_setup: {net_setup}, capture_sec: {CAPTURE_SEC}")
print(f"[INFO] Mobility: model={sc['mobility']['model']}, seed_stream=mobility")
if trace_manifest is not None:
    print(f"[INFO] Mobility trace: policy={TRACE_POLICY}, interval={trace_interval}s, duration={trace_duration}s, combined_sha256={trace_manifest['combined_sha256']}")
print(f"[INFO] GPS: interval={GPS_INTERVAL_S}s, duration={GPS_LOG_SEC}s, gps_logger={gps_logger_path}")
