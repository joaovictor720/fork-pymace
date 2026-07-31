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

    classification_status = net.get("classification_status")
    if classification_status == "warning_unclassified":
        print(
            "[WARN] "
            f"{run_dir}: {net.get('total_unclassified_packets', 0)} "
            "frames could not be classified as payload or control; "
            "see the diagnostic JSON fields in summary.csv.",
            file=sys.stderr,
        )
    elif classification_status == "error_classification_residual":
        print(
            "[WARN] "
            f"{run_dir}: packet breakdown is invalid "
            f"(classification_residual={net.get('classification_residual')}).",
            file=sys.stderr,
        )

    row.update(net)
    row.update(conv)

    # -----------------------------
    # Derived metrics
    # -----------------------------
    if row.get("final_total") and row.get("total_packets") is not None:
        try:
            row["packets_per_global_increment"] = row["total_packets"] / row["final_total"]
        except Exception:
            pass

    if row.get("time_to_90pct_s") and row.get("total_packets") is not None:
        row["packets_until_90pct"] = row["total_packets"]

    if len(row) > 1:
        rows.append(row)

if not rows:
    print("No valid runs found.")
    sys.exit(1)

# Fieldnames: união de todas as colunas para não perder campos
fieldnames = []
seen = set()
for r in rows:
    for k in r.keys():
        if k not in seen:
            seen.add(k)
            fieldnames.append(k)

with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
