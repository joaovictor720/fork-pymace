#!/usr/bin/env python3
"""Build per-application run summaries for legacy and spatial workloads."""

import csv
import pathlib
import sys

from parse_convergence import parse_convergence
from parse_network import parse_network_overhead
from spatial_results import parse_spatial_run


def _write_rows(path, rows):
    if not rows:
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_application(root):
    root = pathlib.Path(root)
    rows = []
    spatial_checkpoints = []
    spatial_nodes = []

    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue

        net = parse_network_overhead(run_dir)
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

        spatial_path = run_dir / "spatial_coverage_analysis.json"
        if spatial_path.exists():
            row, checkpoints, node_rows = parse_spatial_run(
                run_dir,
                network=net,
                include_metadata=False,
            )
            spatial_checkpoints.extend(checkpoints)
            spatial_nodes.extend(node_rows)
        else:
            row = {"run": run_dir.name, "workload": "gcounter"}
            row.update(net)
            row.update(parse_convergence(run_dir))

            # Legacy GCounter-only derived metrics. They deliberately do not
            # overload CAR or spatial ground-truth terminology.
            if row.get("final_total") and row.get("total_packets") is not None:
                try:
                    row["packets_per_global_increment"] = (
                        row["total_packets"] / row["final_total"]
                    )
                except Exception:
                    pass
            if row.get("time_to_90pct_s") and row.get("total_packets") is not None:
                row["packets_until_90pct"] = row["total_packets"]

        if len(row) > 2:
            rows.append(row)

    if not rows:
        for name in ("summary.csv", "spatial_car_checkpoints.csv", "spatial_car_nodes.csv"):
            path = root / name
            if path.exists():
                path.unlink()
        return 0

    _write_rows(root / "summary.csv", rows)
    for name, values in (
        ("spatial_car_checkpoints.csv", spatial_checkpoints),
        ("spatial_car_nodes.csv", spatial_nodes),
    ):
        path = root / name
        if values:
            _write_rows(path, values)
        elif path.exists():
            path.unlink()
    return len(rows)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: parse_metrics.py APP_RESULTS_DIR", file=sys.stderr)
        return 2
    count = parse_application(pathlib.Path(argv[0]))
    if count == 0:
        print("No valid runs found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
