import json
import sys

scenario_path = sys.argv[1]
out_path = sys.argv[2]
result_dir = sys.argv[3]

with open(scenario_path) as f:
    sc = json.load(f)

node_cfg = sc["node_config"].copy()
node_count = sc["nodes"]["count"]

# Auto-generate addresses
node_cfg["address"] = {
    str(i): f"10.0.0.{i+1}:5001"
    for i in range(node_count)
}

node_cfg["seed"] = sc["nodes"].get("seed", 0)

node_cfg["log_dir"] = result_dir

with open(out_path, "w") as f:
    json.dump(node_cfg, f, indent=2)
