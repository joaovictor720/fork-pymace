import csv
import json
import pathlib
import sys
from typing import Iterable, List

from parse_convergence import parse_convergence
from parse_network import parse_network_overhead
from parse_trickle_events import parse_trickle_events


def iter_run_dirs(root: pathlib.Path) -> Iterable[pathlib.Path]:
    variant_status_path = root / "variant_status.json"
    if variant_status_path.exists():
        try:
            variant_status = json.loads(variant_status_path.read_text(encoding="utf-8"))
        except Exception:
            variant_status = {}

        expected_runs = variant_status.get("expected_runs", [])
        if isinstance(expected_runs, list):
            for run_id in expected_runs:
                run_dir = root / str(run_id)
                if run_dir.is_dir():
                    yield run_dir
            return

    for run_dir in sorted(root.iterdir()):
        if run_dir.is_dir():
            yield run_dir


def main() -> int:
    root = pathlib.Path(sys.argv[1])
    out_csv = root / "summary.csv"

    rows: List[dict] = []

    for run_dir in iter_run_dirs(root):
        status_path = run_dir / "run_status.json"
        if not status_path.exists():
            continue

        try:
            run_status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not run_status.get("analyzable", False):
            continue

        row = {
            "run": run_dir.name,
            "run_status": run_status.get("status"),
            "pymace_rc": run_status.get("pymace", {}).get("rc"),
            "analyzable": run_status.get("analyzable"),
        }

        net = parse_network_overhead(run_dir)
        conv = parse_convergence(run_dir)
        trickle = parse_trickle_events(run_dir)

        row.update(net)
        row.update(conv)
        row.update(trickle)

        if row.get("final_total") and row.get("total_packets") is not None:
            try:
                row["packets_per_global_increment"] = row["total_packets"] / row["final_total"]
            except Exception:
                pass

        if row.get("time_to_90pct_s") and row.get("total_packets") is not None:
            row["packets_until_90pct"] = row["total_packets"]

        rows.append(row)

    if not rows:
        print("No analyzable runs found.")
        return 1

    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
