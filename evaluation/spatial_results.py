#!/usr/bin/env python3
"""Collect and aggregate spatial-coverage (CAR) experiment results.

The run is the experimental unit for inferential statistics.  Per-node CAR is
exported for diagnostics and descriptive plots, but nodes from one run are not
treated as independent repetitions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


GENERATED_FILES = {
    "runs": "all_spatial_runs.csv",
    "checkpoints": "all_spatial_car_checkpoints.csv",
    "nodes": "all_spatial_car_nodes.csv",
    "usage_checkpoints": "all_spatial_usage_checkpoints.csv",
    "car": "aggregated_spatial_car.csv",
    "tcover": "aggregated_spatial_tcover.csv",
    "overhead": "aggregated_spatial_overhead.csv",
    "usage": "aggregated_spatial_usage_checkpoints.csv",
}

USAGE_CACHE_SCHEMA = "mace_spatial_pcap_checkpoint_metrics_v1"


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> Optional[int]:
    number = _finite_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _offset_slug(offset_s: float) -> str:
    if abs(offset_s) < 1e-12:
        return "tcover"
    value = format(offset_s, ".12g").replace("-", "minus_").replace(".", "p")
    return f"tcover_plus_{value}s"


def _scenario_family(name: str) -> str:
    name = re.sub(r"__expanded$", "", name)
    return re.sub(r"_(?:ip|batman)$", "", name, flags=re.IGNORECASE)


def _canonical_datapoint(value: Any, path: Tuple[str, ...] = ()) -> Any:
    """Remove repetition- and transport-only fields from a scenario.

    The resulting hash groups repetitions and equivalent IP/BATMAN variants,
    while retaining arbitrary future scientific parameters selected by the
    mobility/scenario author.
    """
    if isinstance(value, Mapping):
        result = {}
        for key in sorted(value):
            lower = str(key).lower()
            current = path + (lower,)
            if lower in {
                "seed",
                "central_seed",
                "seed_streams",
                "trace_sha256",
                "manifest_sha256",
                "combined_sha256",
                "log_dir",
                "address",
            }:
                continue
            if not path and lower in {"name", "experiment"}:
                continue
            if current in {("network", "routing"), ("network", "hardif_behavior")}:
                continue
            result[key] = _canonical_datapoint(value[key], current)
        return result
    if isinstance(value, list):
        return [_canonical_datapoint(item, path) for item in value]
    return value


def _scenario_layout(run_dir: Path, results_root: Optional[Path]) -> Dict[str, str]:
    algorithm = run_dir.parent.name
    scenario = run_dir.parent.parent.name
    variant = ""
    if results_root is not None:
        try:
            parts = run_dir.resolve().relative_to(results_root.resolve()).parts
            if len(parts) >= 4 and parts[-4].endswith("__expanded"):
                scenario, variant, algorithm = parts[-4], parts[-3], parts[-2]
            elif len(parts) >= 3:
                scenario, algorithm = parts[-3], parts[-2]
        except ValueError:
            pass
    return {
        "scenario": re.sub(r"__expanded$", "", scenario),
        "scenario_family": _scenario_family(scenario),
        "variant": variant,
        "algorithm": algorithm,
        "run": run_dir.name,
        "run_dir": str(run_dir),
    }


def _metadata(run_dir: Path, results_root: Optional[Path]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = _scenario_layout(run_dir, results_root)
    scenario_path = run_dir / "scenario.json"
    scenario = _load_json(scenario_path) if scenario_path.exists() else {}
    node_path = run_dir / "node_config.json"
    node_config = _load_json(node_path) if node_path.exists() else {}
    manifest_path = run_dir / "mobility_traces" / "manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.exists() else {}

    canonical = _canonical_datapoint(scenario)
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    metadata["datapoint_key"] = hashlib.sha256(canonical_json.encode()).hexdigest()[:16]
    metadata["datapoint_json"] = canonical_json

    nodes = _integer(_nested(scenario, "nodes", "count"))
    if nodes is None:
        nodes = _integer(_nested(manifest, "nodes") and len(manifest["nodes"]))
    area = _nested(scenario, "simulation", "area")
    area_x = area_y = None
    if isinstance(area, Mapping):
        area_x = _finite_number(area.get("x"))
        area_y = _finite_number(area.get("y"))
    elif isinstance(area, Sequence) and not isinstance(area, (str, bytes)) and len(area) >= 2:
        area_x = _finite_number(area[0])
        area_y = _finite_number(area[1])
    area_km2 = (
        area_x * area_y / 1_000_000.0
        if area_x is not None and area_y is not None and area_x > 0 and area_y > 0
        else None
    )

    grid_rows = _integer(_nested(scenario, "grid", "rows"))
    grid_cols = _integer(_nested(scenario, "grid", "cols"))
    central_seed = _integer(node_config.get("central_seed"))
    if central_seed is None:
        central_seed = _integer(manifest.get("central_seed"))
    metadata.update(
        {
            "nodes_cfg": nodes,
            "area_x_m": area_x,
            "area_y_m": area_y,
            "area_km2": area_km2,
            "density_nodes_km2": nodes / area_km2 if nodes is not None and area_km2 else None,
            "grid_rows": grid_rows,
            "grid_cols": grid_cols,
            "grid_cell_count": grid_rows * grid_cols if grid_rows is not None and grid_cols is not None else None,
            "network_routing": _nested(scenario, "network", "routing"),
            "network_range_m": _finite_number(_nested(scenario, "network", "range")),
            "network_error": _finite_number(_nested(scenario, "network", "error")),
            "network_bandwidth_bps": _finite_number(_nested(scenario, "network", "bandwidth")),
            "mobility_model": _nested(scenario, "mobility", "model") or manifest.get("model"),
            "mobility_pause_s": _finite_number(_nested(scenario, "mobility", "pause")),
            "position_poll_interval_ms": _finite_number(node_config.get("position_poll_interval_ms")),
            "dissemination_interval_s": _finite_number(node_config.get("dissemination_interval")),
            "max_datagram_bytes": _integer(node_config.get("max_datagram_bytes")),
            "central_seed": central_seed,
            "trace_hash": manifest.get("combined_sha256"),
        }
    )
    speed = _nested(scenario, "mobility", "speed")
    if isinstance(speed, Sequence) and not isinstance(speed, (str, bytes)):
        values = [_finite_number(item) for item in speed]
        values = [item for item in values if item is not None]
        if values:
            metadata["mobility_speed_min_mps"] = min(values)
            metadata["mobility_speed_max_mps"] = max(values)
    else:
        metadata["mobility_speed_min_mps"] = _finite_number(speed)
        metadata["mobility_speed_max_mps"] = _finite_number(speed)

    labels = []
    for key, label in (
        ("nodes_cfg", "nodes"),
        ("density_nodes_km2", "density"),
        ("grid_cell_count", "cells"),
        ("network_error", "error"),
        ("position_poll_interval_ms", "poll_ms"),
        ("dissemination_interval_s", "diss_s"),
    ):
        if metadata.get(key) is not None:
            labels.append(f"{label}={metadata[key]:g}")
    metadata["datapoint_label"] = ", ".join(labels) or metadata["variant"] or metadata["datapoint_key"]
    return metadata


def _descriptive(values: Iterable[Optional[float]], prefix: str) -> Dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {f"{prefix}_min": None, f"{prefix}_median": None, f"{prefix}_mean": None, f"{prefix}_max": None}
    return {
        f"{prefix}_min": min(clean),
        f"{prefix}_median": statistics.median(clean),
        f"{prefix}_mean": statistics.fmean(clean),
        f"{prefix}_max": max(clean),
    }


def parse_spatial_run(
    run_dir: Path,
    results_root: Optional[Path] = None,
    network: Optional[Mapping[str, Any]] = None,
    include_metadata: bool = True,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse one ``spatial_coverage_analysis.json`` into normalized tables."""
    run_dir = Path(run_dir)
    analysis = _load_json(run_dir / "spatial_coverage_analysis.json")
    trace = analysis.get("trace_validation") or {}
    car = analysis.get("car") or {}
    if not isinstance(trace, Mapping) or not isinstance(car, Mapping):
        raise ValueError(f"{run_dir}: trace_validation and car must be objects")

    metadata = _metadata(run_dir, results_root) if include_metadata else {"run": run_dir.name}
    trace_valid = trace.get("valid") is True
    car_valid = car.get("valid") is True
    summary: Dict[str, Any] = dict(metadata)
    summary.update(
        {
            "workload": "spatial_coverage",
            "analysis_valid": trace_valid and car_valid,
            "trace_valid": trace_valid,
            "car_valid": car_valid,
            "t_cover_s": _finite_number(car.get("t_cover_s", trace.get("t_cover_s"))),
            "coverage_ratio": _finite_number(trace.get("coverage_ratio")),
            "covered_cell_count": _integer(trace.get("covered_cell_count")),
            "total_cell_count": _integer(trace.get("total_cell_count")),
            "trace_node_count": _integer(trace.get("node_count")),
            "trace_sample_count": _integer(trace.get("sample_count")),
            "missing_cell_count": len(trace.get("missing_cell_ids") or []),
            "trace_issue_count": len(trace.get("issues") or []),
            "car_issue_count": len(car.get("issues") or []),
        }
    )
    if network:
        summary.update(network)

    offsets = car.get("checkpoint_offsets_s") or []
    samples = car.get("samples") or []
    if not isinstance(offsets, list) or not isinstance(samples, list):
        raise ValueError(f"{run_dir}: checkpoint offsets and samples must be arrays")
    if len(offsets) != len(samples):
        raise ValueError(f"{run_dir}: {len(offsets)} checkpoint offsets but {len(samples)} CAR samples")

    checkpoint_rows: List[Dict[str, Any]] = []
    node_rows: List[Dict[str, Any]] = []
    if samples and summary["t_cover_s"] is None:
        raise ValueError(f"{run_dir}: CAR samples require a finite T_cover")
    for raw_offset, sample in zip(offsets, samples):
        offset = _finite_number(raw_offset)
        if offset is None or offset < 0 or not isinstance(sample, Mapping):
            raise ValueError(f"{run_dir}: invalid CAR checkpoint")
        sample_time = _finite_number(sample.get("time_s"))
        swarm_car = _finite_number(sample.get("swarm_car"))
        truth_size = _integer(sample.get("ground_truth_size"))
        per_car = sample.get("per_node_car") or {}
        per_size = sample.get("per_node_replica_size") or {}
        if not isinstance(per_car, Mapping) or not isinstance(per_size, Mapping):
            raise ValueError(f"{run_dir}: per-node CAR fields must be objects")

        car_values = {str(node): _finite_number(value) for node, value in per_car.items()}
        replica_sizes = {str(node): _integer(value) for node, value in per_size.items()}
        if sample_time is None or not math.isclose(
            sample_time,
            float(summary["t_cover_s"]) + offset,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(f"{run_dir}: CAR sample time is not T_cover + checkpoint offset")
        if any(value is not None and value < 0 for value in replica_sizes.values()):
            raise ValueError(f"{run_dir}: replica sizes must be non-negative")
        if truth_size is not None and truth_size > 0:
            for node in set(car_values) & set(replica_sizes):
                node_car = car_values[node]
                replica_size = replica_sizes[node]
                if node_car is not None and replica_size is not None and not math.isclose(
                    node_car,
                    replica_size / float(truth_size),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    raise ValueError(f"{run_dir}: inconsistent CAR and replica size for node {node}")
        finite_node_car = [value for value in car_values.values() if value is not None]
        if finite_node_car and (
            swarm_car is None
            or not math.isclose(
                swarm_car,
                statistics.fmean(finite_node_car),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ):
            raise ValueError(f"{run_dir}: swarm CAR is not the mean per-node CAR")
        checkpoint = dict(metadata)
        checkpoint.update(
            {
                "workload": "spatial_coverage",
                "analysis_valid": trace_valid and car_valid,
                "checkpoint_offset_s": offset,
                "checkpoint_label": _offset_slug(offset),
                "time_s": sample_time,
                "t_cover_s": summary["t_cover_s"],
                "ground_truth_size": truth_size,
                "swarm_car": swarm_car,
                "node_count_observed": len(set(car_values) | set(replica_sizes)),
            }
        )
        checkpoint.update(_descriptive(car_values.values(), "node_car"))
        checkpoint.update(_descriptive(replica_sizes.values(), "node_replica_size"))
        checkpoint_rows.append(checkpoint)

        for node in sorted(set(car_values) | set(replica_sizes), key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item)):
            node_row = dict(metadata)
            node_row.update(
                {
                    "workload": "spatial_coverage",
                    "analysis_valid": trace_valid and car_valid,
                    "checkpoint_offset_s": offset,
                    "checkpoint_label": _offset_slug(offset),
                    "time_s": sample_time,
                    "t_cover_s": summary["t_cover_s"],
                    "ground_truth_size": truth_size,
                    "node": node,
                    "node_car": car_values.get(node),
                    "replica_size": replica_sizes.get(node),
                }
            )
            node_rows.append(node_row)

        slug = _offset_slug(offset)
        summary[f"car_swarm_{slug}"] = swarm_car
        summary[f"car_node_min_{slug}"] = checkpoint["node_car_min"]
        summary[f"car_node_median_{slug}"] = checkpoint["node_car_median"]
        summary[f"car_node_max_{slug}"] = checkpoint["node_car_max"]

    summary["checkpoint_count"] = len(checkpoint_rows)
    if checkpoint_rows:
        final = max(checkpoint_rows, key=lambda row: row["checkpoint_offset_s"])
        summary.update(
            {
                "final_checkpoint_offset_s": final["checkpoint_offset_s"],
                "final_swarm_car": final["swarm_car"],
                "final_node_car_min": final["node_car_min"],
                "final_node_car_median": final["node_car_median"],
                "final_node_car_mean": final["node_car_mean"],
                "final_node_car_max": final["node_car_max"],
                "final_ground_truth_size": final["ground_truth_size"],
            }
        )
    return summary, checkpoint_rows, node_rows


def _mean_ci(values: Iterable[Any], prefix: str, confidence: float = 0.95) -> Dict[str, Any]:
    clean = [_finite_number(value) for value in values]
    clean = [value for value in clean if value is not None]
    n = len(clean)
    if not clean:
        return {f"{prefix}_mean": None, f"{prefix}_ci_low": None, f"{prefix}_ci_high": None, f"{prefix}_std": None, f"{prefix}_n": 0}
    mean = statistics.fmean(clean)
    if n == 1:
        return {f"{prefix}_mean": mean, f"{prefix}_ci_low": mean, f"{prefix}_ci_high": mean, f"{prefix}_std": 0.0, f"{prefix}_n": 1}
    std = statistics.stdev(clean)
    try:
        from scipy import stats

        critical = float(stats.t.ppf((1.0 + confidence) / 2.0, n - 1))
    except ImportError:
        # Conservative normal approximation when scipy is unavailable.
        critical = statistics.NormalDist().inv_cdf((1.0 + confidence) / 2.0)
    margin = critical * std / math.sqrt(n)
    return {f"{prefix}_mean": mean, f"{prefix}_ci_low": mean - margin, f"{prefix}_ci_high": mean + margin, f"{prefix}_std": std, f"{prefix}_n": n}


def _group(rows: Iterable[Dict[str, Any]], keys: Sequence[str]):
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(key) for key in keys)].append(row)
    return groups


def _identity(row: Mapping[str, Any]) -> Dict[str, Any]:
    keys = (
        "scenario_family", "datapoint_key", "datapoint_label", "datapoint_json",
        "algorithm", "nodes_cfg", "area_x_m", "area_y_m", "area_km2",
        "density_nodes_km2", "grid_rows", "grid_cols", "grid_cell_count",
        "network_range_m", "network_error", "network_bandwidth_bps",
        "mobility_model", "mobility_pause_s", "mobility_speed_min_mps",
        "mobility_speed_max_mps", "position_poll_interval_ms",
        "dissemination_interval_s", "max_datagram_bytes",
    )
    return {key: row.get(key) for key in keys}


def aggregate_car(checkpoints: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("scenario_family", "datapoint_key", "algorithm", "checkpoint_offset_s")
    output = []
    for _, group in sorted(_group(checkpoints, keys).items(), key=lambda item: str(item[0])):
        first = group[0]
        row = _identity(first)
        row.update({"checkpoint_offset_s": first.get("checkpoint_offset_s"), "checkpoint_label": first.get("checkpoint_label"), "runs_total": len(group), "runs_valid": sum(item.get("analysis_valid") is True for item in group)})
        valid = [item for item in group if item.get("analysis_valid") is True]
        row.update(_mean_ci((item.get("swarm_car") for item in valid), "swarm_car"))
        row.update(_mean_ci((item.get("node_car_mean") for item in valid), "node_car_mean"))
        row.update(_mean_ci((item.get("node_car_min") for item in valid), "node_car_min"))
        row.update(_mean_ci((item.get("ground_truth_size") for item in valid), "ground_truth_size"))
        output.append(row)
    return output


def aggregate_tcover(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Identical mobility traces evaluated by several algorithms describe one
    # physical coverage event, not several independent T_cover observations.
    physical: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for row in runs:
        if row.get("trace_valid") is not True or _finite_number(row.get("t_cover_s")) is None:
            continue
        replicate = row.get("trace_hash") or f"seed={row.get('central_seed')};run={row.get('run')}"
        key = (row.get("scenario_family"), row.get("datapoint_key"), replicate)
        if key in physical:
            old = float(physical[key]["t_cover_s"])
            new = float(row["t_cover_s"])
            if not math.isclose(old, new, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"inconsistent T_cover for shared trace {replicate}: {old} != {new}")
            physical[key]["source_algorithms"].add(str(row.get("algorithm")))
        else:
            value = dict(row)
            value["source_algorithms"] = {str(row.get("algorithm"))}
            physical[key] = value

    keys = ("scenario_family", "datapoint_key")
    output = []
    for _, group in sorted(_group(physical.values(), keys).items(), key=lambda item: str(item[0])):
        row = _identity(group[0])
        row.pop("algorithm", None)
        row["trace_repetitions"] = len(group)
        row["algorithm_observations"] = sum(len(item["source_algorithms"]) for item in group)
        row.update(_mean_ci((item.get("t_cover_s") for item in group), "t_cover_s"))
        output.append(row)
    return output


def aggregate_overhead(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("scenario_family", "datapoint_key", "algorithm")
    output = []
    for _, group in sorted(_group(runs, keys).items(), key=lambda item: str(item[0])):
        row = _identity(group[0])
        row["runs_total"] = len(group)
        row["runs_valid"] = sum(item.get("analysis_valid") is True for item in group)
        row["success_rate"] = row["runs_valid"] / len(group) if group else None
        valid = [item for item in group if item.get("analysis_valid") is True]
        for field in (
            "final_swarm_car", "total_packets", "total_bytes", "total_payload_packets",
            "total_control_packets", "total_unclassified_packets", "avg_packets_per_node",
        ):
            row.update(_mean_ci((item.get(field) for item in valid), field))
        output.append(row)
    return output


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _write_usage_cache(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "schema", "checkpoint_offset_s", "total_packets", "total_bytes",
        "pcap_count",
    )
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _usage_cache_is_current(cache: Path, dependencies: Sequence[Path]) -> bool:
    if not cache.exists():
        return False
    try:
        cache_mtime = cache.stat().st_mtime_ns
    except OSError:
        return False
    for dependency in dependencies:
        try:
            if dependency.stat().st_mtime_ns > cache_mtime:
                return False
        except OSError:
            return False
    return True


def _usage_cache_path(run_dir: Path, cache_root: Optional[Path]) -> Optional[Path]:
    if cache_root is None:
        return None
    try:
        key = str(run_dir.resolve())
    except OSError:
        key = str(run_dir)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return cache_root / f"{digest}.csv"


def _usage_rows_from_cache(cache: Path, offsets: Sequence[float]) -> Optional[List[Dict[str, Any]]]:
    try:
        rows = _read_csv_rows(cache)
    except (OSError, csv.Error):
        return None
    if not rows or any(row.get("schema") != USAGE_CACHE_SCHEMA for row in rows):
        return None
    by_offset: Dict[float, Dict[str, str]] = {}
    for row in rows:
        offset = _finite_number(row.get("checkpoint_offset_s"))
        if offset is not None:
            by_offset[offset] = row
    if any(offset not in by_offset for offset in offsets):
        return None
    result = []
    for offset in offsets:
        row = by_offset[offset]
        result.append({
            "schema": USAGE_CACHE_SCHEMA,
            "checkpoint_offset_s": offset,
            "total_packets": _integer(row.get("total_packets")),
            "total_bytes": _integer(row.get("total_bytes")),
            "pcap_count": _integer(row.get("pcap_count")),
        })
    return result


def _load_apps_config(root: Path) -> Dict[str, Any]:
    apps_path = root / "evaluation" / "apps.json"
    return json.loads(apps_path.read_text(encoding="utf-8"))


def _pcap_usage_for_offsets(
    run_dir: Path,
    algorithm: str,
    t_cover_s: float,
    offsets: Sequence[float],
    trace_start_unix_s: float,
    apps_config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    from process_pcaps import display_filter_for_app, _run_tshark_rows

    pcaps = sorted(run_dir.glob("node_*.pcap"))
    if not pcaps:
        return []

    display_filter = display_filter_for_app(algorithm, dict(apps_config))
    cutoffs = [(offset, trace_start_unix_s + t_cover_s + offset) for offset in offsets]
    packets = {offset: 0 for offset in offsets}
    bytes_by_offset = {offset: 0 for offset in offsets}

    for pcap in pcaps:
        proc = _run_tshark_rows(
            str(pcap),
            display_filter,
            ["frame.time_epoch", "frame.len"],
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                timestamp = float(parts[0].strip())
                frame_len = int(parts[1].strip())
            except ValueError:
                continue
            for offset, cutoff in cutoffs:
                if timestamp <= cutoff:
                    packets[offset] += 1
                    bytes_by_offset[offset] += frame_len
        assert proc.stderr is not None
        stderr = proc.stderr.read().strip()
        rc = proc.wait()
        if rc != 0:
            message = f"tshark failed for {pcap} with rc={rc}"
            if stderr:
                message += f": {stderr}"
            raise ValueError(message)

    return [
        {
            "schema": USAGE_CACHE_SCHEMA,
            "checkpoint_offset_s": offset,
            "total_packets": packets[offset],
            "total_bytes": bytes_by_offset[offset],
            "pcap_count": len(pcaps),
        }
        for offset in offsets
    ]


def _run_usage_checkpoints(
    run: Mapping[str, Any],
    offsets: Sequence[float],
    apps_config: Mapping[str, Any],
    repo_root: Path,
    cache_root: Optional[Path],
) -> List[Dict[str, Any]]:
    run_dir_text = run.get("run_dir")
    if not run_dir_text:
        return []
    run_dir = Path(str(run_dir_text))
    if not run_dir.exists():
        return []
    t_cover_s = _finite_number(run.get("t_cover_s"))
    if t_cover_s is None:
        return []
    clock_path = run_dir / "experiment_clock.json"
    if not clock_path.exists():
        return []
    clock = _load_json(clock_path)
    trace_start_unix_s = _finite_number(clock.get("trace_start_unix_s"))
    if trace_start_unix_s is None:
        return []

    offsets = tuple(sorted({float(offset) for offset in offsets}))
    if not offsets:
        return []

    cache = _usage_cache_path(run_dir, cache_root)
    pcaps = sorted(run_dir.glob("node_*.pcap"))
    dependencies = pcaps + [
        clock_path,
        run_dir / "spatial_coverage_analysis.json",
        repo_root / "evaluation" / "apps.json",
    ]
    cached = None
    if cache is not None and _usage_cache_is_current(cache, dependencies):
        cached = _usage_rows_from_cache(cache, offsets)
    rows = cached
    if rows is None:
        rows = _pcap_usage_for_offsets(
            run_dir,
            str(run.get("algorithm")),
            t_cover_s,
            offsets,
            trace_start_unix_s,
            apps_config,
        )
        if rows and cache is not None:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                _write_usage_cache(cache, rows)
            except OSError as exc:
                print(f"[WARN] Could not write usage cache {cache}: {exc}", file=sys.stderr)

    output = []
    for item in rows:
        row = _identity(run)
        row.update({
            "workload": "spatial_coverage",
            "analysis_valid": run.get("analysis_valid"),
            "run": run.get("run"),
            "run_dir": str(run_dir),
            "checkpoint_offset_s": item.get("checkpoint_offset_s"),
            "checkpoint_label": _offset_slug(float(item.get("checkpoint_offset_s"))),
            "t_cover_s": t_cover_s,
            "total_packets": item.get("total_packets"),
            "total_bytes": item.get("total_bytes"),
            "pcap_count": item.get("pcap_count"),
        })
        output.append(row)
    return output


def collect_usage_checkpoints(
    runs: Sequence[Dict[str, Any]],
    checkpoints: Sequence[Dict[str, Any]],
    repo_root: Optional[Path] = None,
    cache_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    apps_config = _load_apps_config(repo_root)
    offsets_by_run_dir: Dict[str, set] = defaultdict(set)
    for checkpoint in checkpoints:
        if checkpoint.get("analysis_valid") is not True:
            continue
        offset = _finite_number(checkpoint.get("checkpoint_offset_s"))
        run_dir = checkpoint.get("run_dir")
        if offset is not None and run_dir:
            offsets_by_run_dir[str(run_dir)].add(offset)

    output = []
    for index, run in enumerate(runs, start=1):
        if run.get("analysis_valid") is not True:
            continue
        run_dir = str(run.get("run_dir"))
        offsets = sorted(offsets_by_run_dir.get(run_dir, ()))
        if not offsets:
            continue
        run_path = Path(run_dir)
        cache = _usage_cache_path(run_path, cache_root)
        if (
            not (cache is not None and cache.exists())
            and not any(run_path.glob("node_*.pcap"))
        ):
            continue
        print(f"[INFO] Usage checkpoints: {index}/{len(runs)} {run_dir}", file=sys.stderr)
        output.extend(_run_usage_checkpoints(run, offsets, apps_config, repo_root, cache_root))
    return output


def aggregate_usage_checkpoints(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("scenario_family", "datapoint_key", "algorithm", "checkpoint_offset_s")
    output = []
    for _, group in sorted(_group(rows, keys).items(), key=lambda item: str(item[0])):
        first = group[0]
        row = _identity(first)
        row.update({
            "checkpoint_offset_s": first.get("checkpoint_offset_s"),
            "checkpoint_label": first.get("checkpoint_label"),
            "runs_total": len(group),
            "runs_valid": sum(item.get("analysis_valid") is True for item in group),
        })
        valid = [item for item in group if item.get("analysis_valid") is True]
        for field in ("total_packets", "total_bytes"):
            row.update(_mean_ci((item.get(field) for item in valid), field))
        output.append(row)
    return output


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> bool:
    if not rows:
        if path.exists():
            path.unlink()
        return False
    fields: List[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return True


def collect_results(results_root: Path, strict: bool = False):
    from parse_network import parse_network_overhead

    runs: List[Dict[str, Any]] = []
    checkpoints: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    errors = []
    for analysis_path in sorted(Path(results_root).rglob("spatial_coverage_analysis.json")):
        run_dir = analysis_path.parent
        try:
            run, run_checkpoints, run_nodes = parse_spatial_run(
                run_dir,
                results_root=Path(results_root),
                network=parse_network_overhead(run_dir),
            )
            runs.append(run)
            checkpoints.extend(run_checkpoints)
            nodes.extend(run_nodes)
        except Exception as exc:
            if strict:
                raise
            errors.append(f"{run_dir}: {exc}")
    return runs, checkpoints, nodes, errors


def write_results(results_root: Path, output_dir: Path, strict: bool = False) -> Dict[str, int]:
    runs, checkpoints, nodes, errors = collect_results(results_root, strict=strict)
    for error in errors:
        print(f"[WARN] {error}", file=sys.stderr)
    if not runs:
        print(f"No spatial_coverage_analysis.json files found under {results_root}.")
        return {"runs": 0, "checkpoints": 0, "nodes": 0, "errors": len(errors)}
    usage_checkpoints = collect_usage_checkpoints(
        runs,
        checkpoints,
        cache_root=output_dir / ".spatial_usage_cache",
    )
    tables = {
        "runs": runs,
        "checkpoints": checkpoints,
        "nodes": nodes,
        "usage_checkpoints": usage_checkpoints,
        "car": aggregate_car(checkpoints),
        "tcover": aggregate_tcover(runs),
        "overhead": aggregate_overhead(runs),
        "usage": aggregate_usage_checkpoints(usage_checkpoints),
    }
    for name, rows in tables.items():
        path = output_dir / GENERATED_FILES[name]
        if _write_csv(path, rows):
            print(f"Saved {path} ({len(rows)} rows)")
        else:
            print(f"Skipped {path} (no rows)")
    return {
        "runs": len(runs),
        "checkpoints": len(checkpoints),
        "nodes": len(nodes),
        "usage_checkpoints": len(usage_checkpoints),
        "errors": len(errors),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--strict", action="store_true", help="fail on the first malformed run")
    args = parser.parse_args(argv)
    counts = write_results(args.results_root, args.output_dir, strict=args.strict)
    return 0 if counts["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
