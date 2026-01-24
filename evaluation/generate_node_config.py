import json
import sys

scenario_path = sys.argv[1]
out_path = sys.argv[2]
result_dir = sys.argv[3]

with open(scenario_path, "r", encoding="utf-8") as f:
    sc = json.load(f)

node_cfg = sc["node_config"].copy()
node_count = sc["nodes"]["count"]

udp_port = int(node_cfg.get("udp_port", 5001))
node_cfg["udp_port"] = udp_port

if "diss_per_sec" in node_cfg and node_cfg.get("diss_per_sec") is not None:
    dps = float(node_cfg["diss_per_sec"])
    if dps > 0.0:
        node_cfg["dissemination_interval"] = 1.0 / dps

node_cfg["address"] = {
    str(i): f"10.0.0.{i+1}:{udp_port}"
    for i in range(node_count)
}

node_cfg["seed"] = sc["nodes"].get("seed", 0)
node_cfg["log_dir"] = result_dir

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(node_cfg, f, indent=2)
