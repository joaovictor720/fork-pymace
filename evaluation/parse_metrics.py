import sys
import csv
import json
import pathlib
from typing import Any, Dict

from parse_network import parse_network_overhead
from parse_convergence import parse_convergence

root = pathlib.Path(sys.argv[1])
out_csv = root / "summary.csv"

app = root.name

variant_meta_path = root / "variant_meta.json"
variant_params: Dict[str, Any] = {}
if variant_meta_path.exists():
    try:
        meta = json.loads(variant_meta_path.read_text(encoding="utf-8"))
        variant_params = meta.get("params", {}) if isinstance(meta, dict) else {}
    except Exception:
        variant_params = {}

def flatten_variant_params(params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in params.items():
        short = k.split(".")[-1]
        if short in out:
            out[k] = v
        else:
            out[short] = v
    return out

variant_cols = flatten_variant_params(variant_params)

rows = []

for run_dir in sorted(root.iterdir()):
    if not run_dir.is_dir():
        continue

    row: Dict[str, Any] = {"run": run_dir.name, "app": app}
    row.update(variant_cols)

    net = parse_network_overhead(run_dir)
    conv = parse_convergence(run_dir)

    row.update(net)
    row.update(conv)

    if row.get("final_total") and row.get("total_packets") is not None:
        try:
            row["packets_per_global_increment"] = row["total_packets"] / row["final_total"]
        except Exception:
            pass

    if len(row) > 2:
        rows.append(row)

if not rows:
    print("No valid runs found.")
    sys.exit(1)

fieldnames = []
seen = set()
for r in rows:
    for k in r.keys():
        if k not in seen:
            seen.add(k)
            fieldnames.append(k)

with open(out_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
