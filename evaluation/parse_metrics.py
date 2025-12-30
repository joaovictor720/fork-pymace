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

    row.update(parse_network_overhead(run_dir))
    row.update(parse_convergence(run_dir))

    if len(row) > 1:
        rows.append(row)

if not rows:
    print("No valid runs found.")
    sys.exit(1)

with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
