# evaluation/parse_metrics.py
import sys
import csv
import pathlib
from parse_network import parse_network_overhead
from parse_convergence import parse_convergence

root = pathlib.Path(sys.argv[1])
out_csv = root / "summary.csv"

rows = []

for run_dir in sorted(root.iterdir()):
    if not run_dir.is_dir():
        continue

    row = {"run": run_dir.name}

    net = parse_network_overhead(run_dir)
    conv = parse_convergence(run_dir)

    row.update(net)
    row.update(conv)

    # -----------------------------
    # Derived metrics
    # -----------------------------
    if "final_total" in row and row["final_total"] and "total_packets" in row:
        row["packets_per_global_increment"] = (
            row["total_packets"] / row["final_total"]
        )

    if row.get("time_to_90pct_s") and row.get("total_packets"):
        row["packets_until_90pct"] = row["total_packets"]

    if len(row) > 1:
        rows.append(row)

if not rows:
    print("No valid runs found.")
    sys.exit(1)

with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
