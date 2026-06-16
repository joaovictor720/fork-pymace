import json
import sys
from pathlib import Path

scenario_path = sys.argv[1]
app_name = sys.argv[2]
out_path = sys.argv[3]
result_dir = sys.argv[4]

with open(scenario_path, "r", encoding="utf-8") as f:
    sc = json.load(f)

root = Path(__file__).resolve().parent.parent
apps_path = root / "evaluation" / "apps.json"
apps_cfg = json.loads(apps_path.read_text(encoding="utf-8"))
app_cfg = apps_cfg.get("apps", {}).get(app_name, {})

node_cfg = sc["node_config"].copy()
overrides = app_cfg.get("node_config_overrides", {})
if isinstance(overrides, dict):
    node_cfg.update(overrides)

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
