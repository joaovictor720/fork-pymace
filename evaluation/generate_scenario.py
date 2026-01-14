import json
import sys
import random
import math
from pathlib import Path

APPLICATION_START_DELAY = 30

# -------------------------------------------------
# Helpers for node placement
# -------------------------------------------------

def generate_random_positions(n, area):
    return [(random.uniform(0, area["x"]), random.uniform(0, area["y"])) for _ in range(n)]

def generate_grid_positions(n, area):
    side = math.ceil(math.sqrt(n))
    dx = area["x"] / side
    dy = area["y"] / side
    positions = []
    idx = 0
    for i in range(side):
        for j in range(side):
            if idx >= n:
                break
            positions.append(((i + 0.5) * dx, (j + 0.5) * dy))
            idx += 1
    return positions

# -------------------------------------------------
# Main
# -------------------------------------------------

if len(sys.argv) != 3:
    print("Usage: generate_scenario.py <scenario_dir> <algorithm>")
    sys.exit(1)

scenario_dir = Path(sys.argv[1])
algo = sys.argv[2]   # "rapid" ou "broadcast"

scenario_file = scenario_dir / "scenario.json"
out_file = scenario_dir / "mace.json"

if not scenario_file.exists():
    raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

with open(scenario_file) as f:
    sc = json.load(f)

# ----------------------------
# RNG seed
# ----------------------------
seed = sc["nodes"].get("seed", 0)
random.seed(seed)

# ----------------------------
# Basic parameters
# ----------------------------
node_count = sc["nodes"]["count"]
distribution = sc["nodes"].get("distribution", "random")
raw_area = sc["simulation"]["area"]

if isinstance(raw_area, (list, tuple)):
    area = {"x": raw_area[0], "y": raw_area[1]}
else:
    area = raw_area

# ----------------------------
# Generate node positions
# ----------------------------
if distribution == "random":
    positions = generate_random_positions(node_count, area)
elif distribution == "grid":
    positions = generate_grid_positions(node_count, area)
else:
    raise ValueError(f"Unknown node distribution: {distribution}")

# ----------------------------
# Build node objects
# ----------------------------
nodes = []

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
        "pause": mob.get("pause", 0)
    }

    # ----------------------------
    # Function: depende do algoritmo
    # ----------------------------
    if algo == "broadcast":
        function = [
			f"/bin/bash -lc \""
			f"set -x; "
			f"sleep {APPLICATION_START_DELAY}; "

			# Setup BATMAN
			f"sudo ip addr flush dev eth0; "
			f"sudo ip link set up dev eth0; "
			f"sudo batctl if add eth0; "
			f"sudo ip link set up dev bat0; "
			f"sudo ip addr add 10.0.0.{i+1}/24 dev bat0; "

			# Discover result dir safely
			f"RESULT_DIR=\\$(grep '\\\"log_dir\\\"' __CRDT_NODE_CONFIG__ | "
			f"sed -E 's/.*\\\"log_dir\\\"[[:space:]]*:[[:space:]]*\\\"([^\\\"]+)\\\".*/\\1/'); "

			f"LOG_FILE=\\\"\\$RESULT_DIR/node_{i}.batman_net.log\\\"; "

			# Measurement start
			f"TX_START=\\$(cat /sys/class/net/bat0/statistics/tx_packets); "
			f"RX_START=\\$(cat /sys/class/net/bat0/statistics/rx_packets); "
			f"echo \\\"BATMAN_TX_START=\\$TX_START\\\" > \\\"\\$LOG_FILE\\\"; "
			f"echo \\\"BATMAN_RX_START=\\$RX_START\\\" >> \\\"\\$LOG_FILE\\\"; "

			# Run application
			f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__; "

			# Measurement end
			f"TX_END=\\$(cat /sys/class/net/bat0/statistics/tx_packets); "
			f"RX_END=\\$(cat /sys/class/net/bat0/statistics/rx_packets); "
			f"echo \\\"BATMAN_TX_END=\\$TX_END\\\" >> \\\"\\$LOG_FILE\\\"; "
			f"echo \\\"BATMAN_RX_END=\\$RX_END\\\" >> \\\"\\$LOG_FILE\\\"\""
		]
    elif algo == "rapid":
        function = [
            f"/bin/bash -lc \""
            f"set -x; "
            f"sleep {APPLICATION_START_DELAY}; "

            # Reset iptables
            f"sudo iptables -F; "

            # Add rules to count only RAPID traffic (UDP 5001)
            f"sudo iptables -I OUTPUT -p udp --sport 5001 -j ACCEPT; "
            f"sudo iptables -I INPUT  -p udp --dport 5001 -j ACCEPT; "
            f"sudo iptables -Z; "

            # Discover result dir
            f"RESULT_DIR=\\$(grep '\\\"log_dir\\\"' __CRDT_NODE_CONFIG__ | "
            f"sed -E 's/.*\\\"log_dir\\\"[[:space:]]*:[[:space:]]*\\\"([^\\\"]+)\\\".*/\\1/'); "
            f"LOG_FILE=\\\"\\$RESULT_DIR/node_{i}.rapid_net.log\\\"; "

            # Measurement start
            f"TX_START=\\$(sudo iptables -nvx -L OUTPUT | awk '/udp spt:5001/ {{print \\$1}}'); "
            f"RX_START=\\$(sudo iptables -nvx -L INPUT  | awk '/udp dpt:5001/ {{print \\$1}}'); "
            f"echo \\\"RAPID_TX_START=\\$TX_START\\\" >  \\\"\\$LOG_FILE\\\"; "
            f"echo \\\"RAPID_RX_START=\\$RX_START\\\" >> \\\"\\$LOG_FILE\\\"; "

            # Run application
            f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__; "

            # Measurement end
            f"TX_END=\\$(sudo iptables -nvx -L OUTPUT | awk '/udp spt:5001/ {{print \\$1}}'); "
            f"RX_END=\\$(sudo iptables -nvx -L INPUT  | awk '/udp dpt:5001/ {{print \\$1}}'); "
            f"echo \\\"RAPID_TX_END=\\$TX_END\\\" >> \\\"\\$LOG_FILE\\\"; "
            f"echo \\\"RAPID_RX_END=\\$RX_END\\\" >> \\\"\\$LOG_FILE\\\"\""
        ]
    else:
        raise ValueError(f"Unknown algorithm: {algo}")

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

# ----------------------------
# Build MACE config
# ----------------------------
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
        "emane_location": "/usr/share/emane"
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

with open(out_file, "w") as f:
    json.dump(mace, f, indent=2)

print(f"[OK] Generated {out_file}")
print(f"[INFO] Nodes: {node_count}, distribution: {distribution}, seed: {seed}, algo: {algo}")
