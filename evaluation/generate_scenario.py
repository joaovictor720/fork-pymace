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
area = sc["simulation"]["area"]

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
            f"sleep 5 && $CRDT_BIN -id 1.{i} -config $CRDT_NODE_CONFIG"
        ],
        "extra": {
            "network": ["mesh"],
            "mobility": sc["mobility"]
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
        "report_folder": "results/"
    },
    "networks": [
        {
            "name": "mesh",
            "prefix": "10.0.0.0/24",
            "routing": sc["network"]["routing"],
            "settings": {
                "range": str(sc["network"]["range"]),
                "bandwidth": sc["network"]["bandwidth"],
                "delay": sc["network"]["delay"],
                "jitter": sc["network"]["jitter"],
                "error": sc["network"]["error"],
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
