import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Optional


PARAM_KEYS = {
    "node_config.trickle_k": "k",
    "node_config.trickle_imax_ticks": "imax_ticks",
    "node_config.diss_per_sec": "diss_per_sec",
}

VARIANT_NAME_PARAM_KEYS = {
    "trickle_k": "node_config.trickle_k",
    "trickle_imax_ticks": "node_config.trickle_imax_ticks",
    "diss_per_sec": "node_config.diss_per_sec",
}

METRICS = {
    "avg_final_coverage": "convergence",
    "min_final_coverage": "min_convergence",
    "time_to_99pct_s": "T99",
    "time_to_90pct_s": "T90",
    "convergence_time_s": "sync_time",
    "avg_packets_per_node": "packets_node",
    "avg_bytes_per_node": "bytes_node",
    "trickle_summary_send_per_node": "summary_node",
    "trickle_update_send_per_node": "update_node",
    "trickle_suppressed_per_node": "suppressed_node",
    "trickle_reset_per_node": "reset_node",
    "trickle_interval_change_per_node": "interval_change_node",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate Trickle calibration variants and select latency/overhead profiles."
    )
    parser.add_argument(
        "results_root",
        type=Path,
        help="Directory like results/trickle_calibration_ip__expanded",
    )
    parser.add_argument("--app", default="trickle")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def finite_float(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def mean_numeric(rows: Iterable[dict], key: str) -> Optional[float]:
    values = []
    for row in rows:
        value = finite_float(row.get(key))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return mean(values)


def read_csv_rows(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_variant_value(raw: str) -> object:
    try:
        parsed = float(raw)
    except ValueError:
        return raw
    if parsed.is_integer():
        return int(parsed)
    return parsed


def params_from_variant_name(variant_name: str) -> Dict[str, object]:
    params: Dict[str, object] = {}
    for part in variant_name.split("__"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        param_key = VARIANT_NAME_PARAM_KEYS.get(key)
        if param_key is not None:
            params[param_key] = parse_variant_value(raw_value)
    return params


def load_variant_params(variant_dir: Path, app: str) -> Dict[str, object]:
    for meta_path in (variant_dir / "variant_meta.json", variant_dir / app / "variant_meta.json"):
        if not meta_path.exists():
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        params = meta.get("params", {})
        if params:
            return params

    return params_from_variant_name(variant_dir.name)


def variant_record(variant_dir: Path, app: str) -> Optional[dict]:
    summary_path = variant_dir / app / "summary.csv"
    if not summary_path.exists():
        return None

    run_rows = read_csv_rows(summary_path)
    if not run_rows:
        return None

    params = load_variant_params(variant_dir, app)
    record = {
        "variant": variant_dir.name,
        "runs": len(run_rows),
        "profile": "",
    }

    for param_key, out_key in PARAM_KEYS.items():
        record[out_key] = params.get(param_key)

    for metric_key, out_key in METRICS.items():
        record[out_key] = mean_numeric(run_rows, metric_key)

    for latency_key in ("T90", "T99", "sync_time"):
        value = finite_float(record.get(latency_key))
        if value is not None and value < 0:
            record[latency_key] = 0.0

    converged_values = []
    for row in run_rows:
        raw = str(row.get("converged", "")).strip().lower()
        if raw in {"true", "1"}:
            converged_values.append(1.0)
        elif raw in {"false", "0"}:
            converged_values.append(0.0)
    record["converged_rate"] = mean(converged_values) if converged_values else None

    return record


def metric_key(record: dict, key: str, nan_last: bool = True):
    value = finite_float(record.get(key))
    if value is None:
        return (1 if nan_last else -1, 0.0)
    return (0, value)


def choose_profiles(records: List[dict]) -> Dict[str, dict]:
    usable = [r for r in records if finite_float(r.get("convergence")) is not None]
    if not usable:
        return {}

    best_convergence = max(float(r["convergence"]) for r in usable)
    threshold = 0.95 * best_convergence
    eligible = [
        r for r in usable
        if finite_float(r.get("convergence")) is not None
        and float(r["convergence"]) >= threshold
    ]

    if not eligible:
        return {}

    latency = sorted(
        eligible,
        key=lambda r: (
            metric_key(r, "sync_time"),
            metric_key(r, "T99"),
            metric_key(r, "packets_node"),
            -float(finite_float(r.get("convergence")) or 0.0),
        ),
    )[0]

    overhead = sorted(
        eligible,
        key=lambda r: (
            metric_key(r, "packets_node"),
            metric_key(r, "bytes_node"),
            metric_key(r, "T99"),
            -float(finite_float(r.get("convergence")) or 0.0),
        ),
    )[0]

    return {"latency": latency, "overhead": overhead}


def annotate_profiles(records: List[dict], selected: Dict[str, dict]) -> None:
    by_variant = {}
    for profile, record in selected.items():
        by_variant.setdefault(record["variant"], []).append(profile)

    for record in records:
        profiles = by_variant.get(record["variant"], [])
        record["profile"] = ",".join(profiles)


def write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_plot(path: Path, rows: List[dict]) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"[WARN] Could not import matplotlib; skipping plot: {exc}")
        return

    points = [
        r for r in rows
        if finite_float(r.get("packets_node")) is not None
        and finite_float(r.get("convergence")) is not None
    ]
    if not points:
        print("[WARN] No points with packets_node and convergence; skipping plot.")
        return

    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = [float(r["packets_node"]) for r in points]
    y = [float(r["convergence"]) for r in points]
    colors = [
        "#d62728" if "latency" in str(r.get("profile", ""))
        else "#2ca02c" if "overhead" in str(r.get("profile", ""))
        else "#1f77b4"
        for r in points
    ]

    ax.scatter(x, y, c=colors)
    for r in points:
        if r.get("profile"):
            label = f"{r['profile']}: k={r['k']}, Imax={r['imax_ticks']}, d={r['diss_per_sec']}"
            ax.annotate(label, (float(r["packets_node"]), float(r["convergence"])), fontsize=8)

    ax.set_xlabel("packets/node")
    ax.set_ylabel("final convergence")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    results_root = args.results_root
    out_dir = args.out_dir or results_root
    out_dir.mkdir(parents=True, exist_ok=True)

    if not results_root.exists():
        print(f"[ERROR] Missing results root: {results_root}")
        return 1

    records = []
    for variant_dir in sorted(p for p in results_root.iterdir() if p.is_dir()):
        record = variant_record(variant_dir, args.app)
        if record is not None:
            records.append(record)

    if not records:
        print(f"[ERROR] No calibration summary.csv files found under {results_root}")
        return 1

    selected = choose_profiles(records)
    if not selected:
        print("[ERROR] Could not select profiles; missing convergence metrics.")
        return 1

    annotate_profiles(records, selected)
    selected_rows = []
    for profile, record in selected.items():
        selected_record = dict(record)
        selected_record["profile"] = profile
        selected_rows.append(selected_record)

    summary_path = out_dir / "trickle_calibration_summary.csv"
    selected_path = out_dir / "trickle_calibration_selected.csv"
    plot_path = out_dir / "trickle_calibration_scatter.png"

    write_csv(summary_path, records)
    write_csv(selected_path, selected_rows)
    write_plot(plot_path, records)

    print(f"[OK] Wrote {summary_path}")
    print(f"[OK] Wrote {selected_path}")
    print(f"[OK] Wrote {plot_path}")

    for profile, record in selected.items():
        print(
            f"{profile}: k={record.get('k')} "
            f"imax_ticks={record.get('imax_ticks')} "
            f"diss_per_sec={record.get('diss_per_sec')} "
            f"convergence={record.get('convergence')} "
            f"packets/node={record.get('packets_node')} "
            f"T99={record.get('T99')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
