import sys
import csv
import pathlib

root = pathlib.Path(sys.argv[1])
out_csv = root / "summary.csv"

rows = []

for run_dir in root.iterdir():
    if not run_dir.is_dir():
        continue

    final_total = None
    sent_msgs = 0
    recv_msgs = 0
    sent_bytes = 0
    recv_bytes = 0

    for log in run_dir.glob("node_*.log"):
        with open(log) as f:
            for line in f:
                if "total=" not in line:
                    continue

                parts = [p.strip() for p in line.strip().split(",")]

                for p in parts:
                    if p.startswith("total="):
                        final_total = int(p.split("=")[1])
                    elif p.startswith("sent_msgs="):
                        sent_msgs += int(p.split("=")[1])
                    elif p.startswith("recv_msgs="):
                        recv_msgs += int(p.split("=")[1])
                    elif p.startswith("sent_bytes="):
                        sent_bytes += int(p.split("=")[1])
                    elif p.startswith("recv_bytes="):
                        recv_bytes += int(p.split("=")[1])

    if final_total is not None:
        rows.append({
            "run": run_dir.name,
            "final_total": final_total,
            "sent_msgs": sent_msgs,
            "recv_msgs": recv_msgs,
            "sent_bytes": sent_bytes,
            "recv_bytes": recv_bytes,
        })

if not rows:
    print("No valid runs found.")
    sys.exit(1)

with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
