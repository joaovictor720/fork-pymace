import json
import sys
import os

base_cfg_path = sys.argv[1]
result_dir = sys.argv[2]
out_path = sys.argv[3]

def load_cfg(path):
    with open(path) as f:
        return json.load(f)

cfg = load_cfg(base_cfg_path)

# Handle inheritance
if "extends" in cfg:
    parent_path = os.path.join(os.path.dirname(base_cfg_path), cfg["extends"])
    parent = load_cfg(parent_path)
    parent.update(cfg)
    cfg = parent
    cfg.pop("extends", None)

cfg["log_dir"] = result_dir

with open(out_path, "w") as f:
    json.dump(cfg, f, indent=2)
