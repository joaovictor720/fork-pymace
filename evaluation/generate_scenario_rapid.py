import json
import sys
import random
import math
from pathlib import Path

# -------------------------------------------------
# Helpers for node placement
# -------------------------------------------------

def generate_random_positions(n, area):
    return [
        (
            random.uniform(0, area["x"]),
            random.uniform(0, area["y"])
        )
        for _ in range(n)
    ]


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
            positions.append((
                (i + 0.5) * dx,
                (j + 0.5) * dy
            ))
            idx += 1
    return positions


# -------------------------------------------------
# Main
# -------------------------------------------------

scenario_dir = Path(sys.argv[1])
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

if isinstance(raw_area, list) or isinstance(raw_area, tuple):
    area = {
        "x": raw_area[0],
        "y": raw_area[1]
    }
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
        "function": [
            f"/bin/bash -lc \"set -x; sleep {sc['simulation']['start_delay']}; "
            f"__CRDT_BIN__ -id {i} -config __CRDT_NODE_CONFIG__\""
        ],
        "extra": {
            # ---- REQUIRED by MACE ----
            "disks": "False",

            "dump": {
                "start": "False",
                "delay": 0,
                "duration": 0
            },

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
        # ---- REQUIRED by MACE ----
        "core": "True",
        "omnet": "False",
        "dump": "False",
        "number_of_nodes": node_count,
        "start_delay": sc["simulation"]["start_delay"],
        "runtime": sc["simulation"]["duration"],

        # ---- REQUIRED, even if EMANE is disabled ----
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

# ----------------------------
# Write output
# ----------------------------
with open(out_file, "w") as f:
    json.dump(mace, f, indent=2)

print(f"[OK] Generated {out_file}")
print(f"[INFO] Nodes: {node_count}, distribution: {distribution}, seed: {seed}")
