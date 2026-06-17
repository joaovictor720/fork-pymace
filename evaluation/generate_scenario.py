import argparse
import json
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a pymace scenario from evaluation/scenario.json plus evaluation/apps.json."
    )
    parser.add_argument("scenario_dir", help="Scenario directory containing scenario.json")
    parser.add_argument("app", help="App name from evaluation/apps.json")
    parser.add_argument(
        "--out",
        dest="out_path",
        help="Optional output path for mace.json. Defaults to <scenario_dir>/mace.json.",
    )
    return parser.parse_args()


def generate_random_positions(n: int, area: Dict[str, float]) -> List[Tuple[float, float]]:
    return [(random.uniform(0, area["x"]), random.uniform(0, area["y"])) for _ in range(n)]


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


def main() -> None:
    args = parse_args()

    scenario_dir = Path(args.scenario_dir)
    app = args.app

    root = Path(__file__).resolve().parent.parent
    apps_path = root / "evaluation" / "apps.json"
    apps_cfg = json.loads(apps_path.read_text(encoding="utf-8"))
    apps = apps_cfg.get("apps", {})
    if app not in apps:
        raise SystemExit(f"[ERROR] App not found in apps.json: {app}")
    app_cfg = apps[app]

    scenario_file = scenario_dir / "scenario.json"
    out_file = Path(args.out_path) if args.out_path else (scenario_dir / "mace.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if not scenario_file.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

    with scenario_file.open("r", encoding="utf-8") as f:
        sc = json.load(f)

    seed = sc["nodes"].get("seed", 0)
    random.seed(seed)

    node_count = sc["nodes"]["count"]
    distribution = sc["nodes"].get("distribution", "random")
    raw_area = sc["simulation"]["area"]

    if isinstance(raw_area, (list, tuple)):
        area = {"x": float(raw_area[0]), "y": float(raw_area[1])}
    else:
        area = {"x": float(raw_area["x"]), "y": float(raw_area["y"])}

    if distribution == "random":
        positions = generate_random_positions(node_count, area)
    elif distribution == "grid":
        positions = generate_grid_positions(node_count, area)
    else:
        raise ValueError(f"Unknown node distribution: {distribution}")

    nodes: List[Dict[str, Any]] = []
    net_setup = str(app_cfg.get("net_setup", "ip")).lower()
    tcpdump_filter = str(app_cfg.get("tcpdump_filter", "")).strip()

    node_cfg = sc.get("node_config", {})
    duration_s = float(node_cfg.get("duration", 10))
    cooldown_s = float(node_cfg.get("cooldown", 10))

    CAPTURE_SEC = int(math.ceil(duration_s + cooldown_s + 1.0))

    GPS_INTERVAL_S = float(node_cfg.get("gps_interval", 0.5))
    GPS_LOG_SEC = float(node_cfg.get("gps_duration", duration_s + cooldown_s))

    gps_logger_path = root / "evaluation" / "gps_logger.py"

    for i, (x, y) in enumerate(positions):
        mob = sc["mobility"]
        if "speed" in mob:
            vmin, vmax = mob["speed"]
        else:
            vmin = mob.get("speed_min", 0)
            vmax = mob.get("speed_max", 0)

        mobility = {
            "model": mob["model"],
            "zone_x": area["x"],
            "zone_y": area["y"],
            "zone_z": 0,
            "velocity_lower": vmin,
            "velocity_upper": vmax,
            "pause": mob.get("pause", 0),
        }
        if "aggregation" in mob:
            mobility["aggregation"] = mob["aggregation"]
        if "epoch" in mob:
            mobility["epoch"] = mob["epoch"]

        if net_setup == "batman":
            base_net_setup = (
                f"sudo ip addr flush dev eth0; "
                f"sudo ip link set up dev eth0; "
                f"sudo batctl if add eth0; "
                f"sudo ip link set up dev bat0; "
                f"sudo ip addr add 10.0.0.{i + 1}/24 dev bat0; "
            )
        else:
            base_net_setup = "sudo ip link set up dev eth0; "

        function = [
            f"/bin/bash -lc \""
            f"ulimit -c 0; "
            f"set -x; "
            f"{base_net_setup}"
            f"RESULT_DIR=\\$(grep '\\\"log_dir\\\"' __CRDT_NODE_CONFIG__ | "
            f"sed -E 's/.*\\\"log_dir\\\"[[:space:]]*:[[:space:]]*\\\"([^\\\"]+)\\\".*/\\1/'); "
            f"LOG_FILE=\\\"\\$RESULT_DIR/node_{i}.net.log\\\"; "
            f"PCAP_FILE=\\\"\\$RESULT_DIR/node_{i}.pcap\\\"; "
            f"TCPDUMP_ERR=\\\"\\$RESULT_DIR/node_{i}.tcpdump.stderr\\\"; "
            f"GPS_FILE=\\\"\\$RESULT_DIR/node_{i}.gps.csv\\\"; "
            f"GPS_ERR=\\\"\\$RESULT_DIR/node_{i}.gps.stderr\\\"; "
            f"echo \\\"APP={app}\\\" > \\\"\\$LOG_FILE\\\"; "
            f"sudo timeout -s INT {CAPTURE_SEC} tcpdump -i eth0 -w \\\"\\$PCAP_FILE\\\" "
            f"'{tcpdump_filter}' >/dev/null 2>\\\"\\$TCPDUMP_ERR\\\" & "
            f"TCPDUMP_PID=\\$!; "
            f"echo \\\"TCPDUMP_PID=\\$TCPDUMP_PID\\\" >> \\\"\\$LOG_FILE\\\"; "
            f"GPS_TAG=\\\"node{i}\\\"; "
            f"/usr/bin/python3 {gps_logger_path} "
            f"--tag \\\"\\$GPS_TAG\\\" --node {i} --out \\\"\\$GPS_FILE\\\" "
            f"--interval {GPS_INTERVAL_S} --duration {GPS_LOG_SEC} "
            f">/dev/null 2>\\\"\\$GPS_ERR\\\" & "
            f"GPS_PID=\\$!; "
            f"echo \\\"GPS_PID=\\$GPS_PID\\\" >> \\\"\\$LOG_FILE\\\"; "
            f"echo \\\"GPS_FILE=\\$GPS_FILE\\\" >> \\\"\\$LOG_FILE\\\"; "
            f"START_TS=\\\"${{PYMACE_START_TS:-}}\\\"; "
            f"echo \\\"PYMACE_START_TS=\\$START_TS\\\" >> \\\"\\$LOG_FILE\\\"; "
            f"if [ -n \\\"\\$START_TS\\\" ]; then "
            f"/usr/bin/python3 -c 'import os,time; s=float(os.environ.get(\"PYMACE_START_TS\", \"0\") or 0); time.sleep(max(0.0, s - time.time()))'; "
            f"fi; "
            f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__; "
            f"APP_RC=\\$?; "
            f"echo \\\"APP_RC=\\$APP_RC\\\" >> \\\"\\$LOG_FILE\\\"; "
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
                "range": sc["network"]["range"],
            },
            "type": "UTM",
            "function": function,
            "extra": {
                "disks": "False",
                "dump": {"start": "False", "delay": 0, "duration": 0},
                "network": ["mesh"],
                "mobility": mobility,
            },
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
            "emane_scale": 1.0,
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
                    "emane": "False",
                },
            }
        ],
        "nodes": nodes,
    }

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(mace, f, indent=2)

    print(f"[OK] Generated {out_file}")
    print(
        f"[INFO] Nodes: {node_count}, distribution: {distribution}, seed: {seed}, "
        f"app: {app}, net_setup: {net_setup}, capture_sec: {CAPTURE_SEC}"
    )
    print(
        f"[INFO] GPS: interval={GPS_INTERVAL_S}s, duration={GPS_LOG_SEC}s, "
        f"gps_logger={gps_logger_path}"
    )


if __name__ == "__main__":
    main()
