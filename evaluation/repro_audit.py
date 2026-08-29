#!/usr/bin/env python3
"""Audit reproducibility manifests and run-to-run metric variation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


METRIC_COLUMNS = (
    "total_packets",
    "total_payload_packets",
    "total_control_packets",
    "final_total",
    "avg_final_coverage",
    "min_final_coverage",
    "avg_abs_error",
    "max_abs_error",
    "packets_per_global_increment",
)


def load_jobs(path: Path) -> List[Dict[str, str]]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    jobs = []
    for raw in cfg.get("jobs", []):
        scenario = str(raw.get("scenario", "")).strip()
        app = str(raw.get("app", "")).strip()
        if scenario and app:
            jobs.append({"scenario": scenario, "app": app})
    return jobs


def scenario_prefix(scenario: str) -> str:
    if scenario.endswith("_batman"):
        return scenario[: -len("_batman")]
    if scenario.endswith("_ip"):
        return scenario[: -len("_ip")]
    return scenario


def unique_join(values: Iterable[Any]) -> str:
    out = []
    for value in values:
        text = "" if value is None else str(value)
        if text and text not in out:
            out.append(text)
    return ";".join(out)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_trace_hash(run_dir: Path) -> Optional[str]:
    trace_dir = run_dir / "mobility_traces"
    if trace_dir.is_dir():
        digest = hashlib.sha256()
        files = sorted(trace_dir.glob("node_*.csv"))
        if files:
            for path in files:
                digest.update(path.name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(sha256_file(path).encode("ascii"))
                digest.update(b"\0")
            return digest.hexdigest()

    mace_path = run_dir / "mace.json"
    if not mace_path.exists():
        return None
    try:
        mace = json.loads(mace_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    digest = hashlib.sha256()
    found = False
    for node in mace.get("nodes", []):
        mobility = node.get("extra", {}).get("mobility", {})
        trace_hash = mobility.get("trace_sha256")
        if trace_hash:
            found = True
            digest.update(str(node.get("name", "")).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(trace_hash).encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest() if found else None


def op_create_signature(run_dir: Path) -> Optional[str]:
    files = sorted(run_dir.glob("node_*.log.events"))
    if not files:
        return None
    counts = []
    for path in files:
        try:
            count = path.read_text(errors="replace").count("event=op_create")
        except OSError:
            count = -1
        counts.append(f"{path.stem}:{count}")
    return ",".join(counts)


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_summary(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as stream:
        return {row.get("run", ""): row for row in csv.DictReader(stream)}


def to_float(value: Any) -> Optional[float]:
    try:
        if value == "" or value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def cv_percent(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    mean = statistics.fmean(clean)
    if mean == 0:
        return 0.0 if all(v == 0 for v in clean) else None
    return statistics.stdev(clean) / abs(mean) * 100.0


def max_delta(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return max(clean) - min(clean)


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def expected_variant_dirs(results_root: Path, scenario: str) -> List[Path]:
    expanded = results_root / f"{scenario}__expanded"
    if expanded.is_dir():
        return sorted(p for p in expanded.iterdir() if p.is_dir())
    direct = results_root / scenario
    return [direct] if direct.is_dir() else []


def audit_job(results_root: Path, scenario: str, app: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for variant_dir in expected_variant_dirs(results_root, scenario):
        app_dir = variant_dir / app
        if not app_dir.is_dir():
            rows.append({
                "scenario": scenario,
                "variant": variant_dir.name,
                "algorithm": app,
                "runs": 0,
                "status": "missing_results",
            })
            continue

        run_dirs = sorted(p for p in app_dir.glob("run_*") if p.is_dir())
        summary = read_summary(app_dir / "summary.csv")

        central_seeds = []
        app_seeds = []
        seed_stream_hashes = []
        trace_hashes = []
        op_signatures = []
        batman_requested = []
        batman_effective = []
        batman_matches = []
        metric_values: Dict[str, List[float]] = {name: [] for name in METRIC_COLUMNS}

        for run_dir in run_dirs:
            node_cfg = read_json(run_dir / "node_config.json")
            central_seeds.append(node_cfg.get("central_seed"))
            app_seeds.append(node_cfg.get("seed"))
            seed_streams = node_cfg.get("seed_streams", {})
            if seed_streams:
                payload = json.dumps(seed_streams, sort_keys=True).encode("utf-8")
                seed_stream_hashes.append(hashlib.sha256(payload).hexdigest())

            trace_hashes.append(combined_trace_hash(run_dir))
            op_signatures.append(op_create_signature(run_dir))

            batman = read_json(run_dir / "batman_module.json")
            if batman:
                batman_requested.append(batman.get("requested_mode"))
                batman_effective.append(batman.get("effective_mode"))
                batman_matches.append(bool(batman.get("mode_matches_request")))

            row = summary.get(run_dir.name, {})
            for metric in METRIC_COLUMNS:
                value = to_float(row.get(metric))
                if value is not None:
                    metric_values[metric].append(value)

        trace_unique = {v for v in trace_hashes if v}
        op_unique = {v for v in op_signatures if v}
        seed_stream_unique = {v for v in seed_stream_hashes if v}
        central_unique = {v for v in central_seeds if v is not None}
        app_seed_unique = {v for v in app_seeds if v is not None}

        manifest_ok = (
            len(run_dirs) > 0
            and len(central_unique) == 1
            and len(app_seed_unique) == 1
            and len(seed_stream_unique) <= 1
            and len(trace_unique) == 1
            and len(op_unique) == 1
        )
        batman_ok = True
        if batman_requested or batman_effective or batman_matches:
            batman_ok = (
                set(batman_requested) == {"emulated_wifi"}
                and set(batman_effective) == {"emulated_wifi"}
                and all(batman_matches)
            )

        row_out: Dict[str, Any] = {
            "scenario": scenario,
            "variant": variant_dir.name,
            "algorithm": app,
            "runs": len(run_dirs),
            "status": "ok" if manifest_ok and batman_ok else "check",
            "central_seeds": unique_join(central_seeds),
            "application_seeds": unique_join(app_seeds),
            "seed_stream_hashes": unique_join(seed_stream_hashes),
            "trace_hashes": unique_join(trace_hashes),
            "trace_identical": len(trace_unique) == 1,
            "workload_identical": len(op_unique) == 1,
            "batman_requested_modes": unique_join(batman_requested),
            "batman_effective_modes": unique_join(batman_effective),
            "batman_all_matches": "" if not batman_matches else all(batman_matches),
        }

        for metric, values in metric_values.items():
            row_out[f"{metric}_cv_pct"] = cv_percent(values)
            row_out[f"{metric}_max_delta"] = max_delta(values)

        rows.append(row_out)

    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "scenario",
        "variant",
        "algorithm",
        "runs",
        "status",
        "central_seeds",
        "application_seeds",
        "seed_stream_hashes",
        "trace_hashes",
        "trace_identical",
        "workload_identical",
        "batman_requested_modes",
        "batman_effective_modes",
        "batman_all_matches",
    ]
    for metric in METRIC_COLUMNS:
        fields.append(f"{metric}_cv_pct")
        fields.append(f"{metric}_max_delta")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: fmt(row.get(field)) for field in fields})


def summarize_cross_app_traces(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if row.get("runs", 0) == 0:
            continue
        key = (scenario_prefix(str(row.get("scenario", ""))), str(row.get("variant", "")))
        grouped.setdefault(key, {"apps": [], "trace_hashes": set()})
        grouped[key]["apps"].append(f"{row.get('scenario')}:{row.get('algorithm')}")
        for trace_hash in str(row.get("trace_hashes", "")).split(";"):
            if trace_hash:
                grouped[key]["trace_hashes"].add(trace_hash)

    mismatches = []
    for (prefix, variant), data in sorted(grouped.items()):
        if len(data["trace_hashes"]) > 1:
            mismatches.append({
                "prefix": prefix,
                "variant": variant,
                "apps": data["apps"],
                "trace_hashes": sorted(data["trace_hashes"]),
            })
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--jobs",
        type=Path,
        default=Path(os.environ.get("JOBS_JSON", "evaluation/jobs.json")),
    )
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/article_repro_audit.csv"),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 when manifest or BATMAN mode checks fail.",
    )
    args = parser.parse_args()

    jobs = load_jobs(args.jobs)
    if not jobs:
        raise SystemExit(f"No jobs found in {args.jobs}")

    rows: List[Dict[str, Any]] = []
    for job in jobs:
        rows.extend(audit_job(args.results, job["scenario"], job["app"]))

    write_csv(args.output, rows)
    mismatches = summarize_cross_app_traces(rows)

    bad_rows = [row for row in rows if row.get("status") != "ok"]
    print(f"Wrote {args.output}")
    print(f"Rows: {len(rows)}")
    print(f"Rows needing check: {len(bad_rows)}")
    print(f"Cross-app trace mismatches: {len(mismatches)}")

    if bad_rows:
        for row in bad_rows[:10]:
            print(
                "CHECK "
                f"{row.get('scenario')}/{row.get('variant')}/{row.get('algorithm')}: "
                f"trace_identical={row.get('trace_identical')} "
                f"workload_identical={row.get('workload_identical')} "
                f"batman_effective={row.get('batman_effective_modes')}"
            )

    if mismatches:
        for item in mismatches[:10]:
            print(
                "TRACE MISMATCH "
                f"{item['prefix']}/{item['variant']}: "
                f"{', '.join(item['apps'])}"
            )

    if args.strict and (bad_rows or mismatches):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
