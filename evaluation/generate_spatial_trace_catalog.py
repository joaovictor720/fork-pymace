#!/usr/bin/env python3
"""Generate deterministic trace sets for the spatial grid experiments.

Each catalog run contains 50 node traces.  Nodes 0..9 execute a randomized
stratified sweep at a fixed movement speed that guarantees full-grid coverage
after the coverage start time; nodes 10..49 execute seeded random patrols to
provide density and connectivity variation.
"""

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
EVALUATION_DIR = ROOT_DIR / "evaluation"
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from mobility_trace import TRACE_POLICY, _format_float, _rows_sha256, file_sha256
from seed_utils import derive_seed
from spatial_coverage import GridSpec, check_traces, read_traces


CATALOG_POLICY = "mace_spatial_trace_catalog_v1"
GENERATOR_POLICY = "stratified_randomized_fixed_speed_sweep_v2"
DEFAULT_CATALOG = (
    ROOT_DIR / "evaluation" / "trace_catalogs" / "spatial_grid_1km_24x24"
)

Point = Tuple[float, float]
TimedPoint = Tuple[float, float, float]


def _distance(left: Point, right: Point) -> float:
    return math.hypot(right[0] - left[0], right[1] - left[1])


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _cell_center(grid: GridSpec, row: int, col: int) -> Point:
    return (
        grid.origin_x_m + (col + 0.5) * grid.cell_width,
        grid.origin_y_m + (row + 0.5) * grid.cell_height,
    )


def _jittered_cell_center(
    grid: GridSpec,
    row: int,
    col: int,
    rng: random.Random,
    jitter_fraction: float,
) -> Point:
    x, y = _cell_center(grid, row, col)
    margin_x = grid.cell_width * jitter_fraction
    margin_y = grid.cell_height * jitter_fraction
    lower_x = grid.origin_x_m + col * grid.cell_width + grid.cell_width * 0.2
    upper_x = grid.origin_x_m + (col + 1) * grid.cell_width - grid.cell_width * 0.2
    lower_y = grid.origin_y_m + row * grid.cell_height + grid.cell_height * 0.2
    upper_y = grid.origin_y_m + (row + 1) * grid.cell_height - grid.cell_height * 0.2
    return (
        _clamp(x + rng.uniform(-margin_x, margin_x), lower_x, upper_x),
        _clamp(y + rng.uniform(-margin_y, margin_y), lower_y, upper_y),
    )


def _split_columns(cols: int, parts: int) -> List[List[int]]:
    base = cols // parts
    extra = cols % parts
    groups = []
    current = 0
    for idx in range(parts):
        width = base + (1 if idx < extra else 0)
        groups.append(list(range(current, current + width)))
        current += width
    return groups


def _coverage_route(
    grid: GridSpec,
    columns: Sequence[int],
    rng: random.Random,
    jitter_fraction: float,
) -> List[Point]:
    route: List[Point] = []
    ordered_cols = list(columns)
    if rng.random() < 0.5:
        ordered_cols.reverse()
    first_col_asc = rng.random() < 0.5
    for col_idx, col in enumerate(ordered_cols):
        ascending = first_col_asc if col_idx % 2 == 0 else not first_col_asc
        rows = range(grid.rows) if ascending else range(grid.rows - 1, -1, -1)
        for row in rows:
            route.append(_jittered_cell_center(grid, row, col, rng, jitter_fraction))
    return route


def _timed_route_at_speed(
    points: Sequence[Point],
    start_s: float,
    speed_mps: float,
) -> List[TimedPoint]:
    if not points:
        raise ValueError("route cannot be empty")
    if speed_mps <= 0.0:
        raise ValueError("route speed must be positive")
    timed: List[TimedPoint] = [(start_s, points[0][0], points[0][1])]
    t = start_s
    previous = points[0]
    for point in points[1:]:
        t += _distance(previous, point) / speed_mps
        timed.append((t, point[0], point[1]))
        previous = point
    return timed


def _append_patrol(
    waypoints: List[TimedPoint],
    *,
    rng: random.Random,
    grid: GridSpec,
    until_s: float,
    speed_range: Tuple[float, float],
    pause_probability: float,
    max_pause_s: float,
) -> None:
    if not waypoints:
        raise ValueError("patrol requires an initial point")
    t, x, y = waypoints[-1]
    while t < until_s:
        target = (
            rng.uniform(grid.origin_x_m, grid.upper_x_m),
            rng.uniform(grid.origin_y_m, grid.upper_y_m),
        )
        speed = rng.uniform(speed_range[0], speed_range[1])
        travel_s = max(_distance((x, y), target) / max(speed, 0.001), 0.001)
        t += travel_s
        x, y = target
        waypoints.append((t, x, y))
        if t < until_s and rng.random() < pause_probability:
            t += rng.uniform(0.2, max_pause_s)
            waypoints.append((t, x, y))


def _coverage_waypoints(
    grid: GridSpec,
    columns: Sequence[int],
    *,
    node_rng: random.Random,
    coverage_start_s: float,
    trace_duration_s: float,
    coverage_speed_mps: float,
    patrol_speed_mps: float,
) -> List[TimedPoint]:
    route = _coverage_route(grid, columns, node_rng, jitter_fraction=0.18)
    route_start = coverage_start_s
    first_x, first_y = route[0]
    warmup_radius = min(
        grid.cell_width * 1.5,
        grid.cell_height * 1.5,
        coverage_speed_mps * max(coverage_start_s, 0.0) * 0.2,
    )

    def nearby_start_point() -> Point:
        angle = node_rng.uniform(0.0, 2.0 * math.pi)
        distance = node_rng.uniform(0.0, warmup_radius)
        return (
            _clamp(
                first_x + math.cos(angle) * distance,
                grid.origin_x_m,
                grid.upper_x_m,
            ),
            _clamp(
                first_y + math.sin(angle) * distance,
                grid.origin_y_m,
                grid.upper_y_m,
            ),
        )

    warmup_start = nearby_start_point()
    warmup_mid = nearby_start_point()
    waypoints: List[TimedPoint] = [
        (0.0, warmup_start[0], warmup_start[1]),
        (coverage_start_s * 0.45, warmup_mid[0], warmup_mid[1]),
        (route_start, first_x, first_y),
    ]
    route_points = _timed_route_at_speed(route, route_start, coverage_speed_mps)
    waypoints.extend(route_points[1:])
    _append_patrol(
        waypoints,
        rng=node_rng,
        grid=grid,
        until_s=trace_duration_s,
        speed_range=(patrol_speed_mps, patrol_speed_mps),
        pause_probability=0.2,
        max_pause_s=1.5,
    )
    return waypoints


def _random_patrol_waypoints(
    grid: GridSpec,
    rng: random.Random,
    trace_duration_s: float,
    speed_mps: float,
) -> List[TimedPoint]:
    start = (
        rng.uniform(grid.origin_x_m, grid.upper_x_m),
        rng.uniform(grid.origin_y_m, grid.upper_y_m),
    )
    waypoints: List[TimedPoint] = [(0.0, start[0], start[1])]
    _append_patrol(
        waypoints,
        rng=rng,
        grid=grid,
        until_s=trace_duration_s,
        speed_range=(speed_mps, speed_mps),
        pause_probability=0.25,
        max_pause_s=2.5,
    )
    return waypoints


def _position_at(waypoints: Sequence[TimedPoint], time_s: float) -> Point:
    if time_s <= waypoints[0][0]:
        return waypoints[0][1], waypoints[0][2]
    for left, right in zip(waypoints, waypoints[1:]):
        lt, lx, ly = left
        rt, rx, ry = right
        if time_s <= rt:
            if rt <= lt:
                return rx, ry
            ratio = (time_s - lt) / (rt - lt)
            return lx + ratio * (rx - lx), ly + ratio * (ry - ly)
    return waypoints[-1][1], waypoints[-1][2]


def _write_trace(
    path: Path,
    waypoints: Sequence[TimedPoint],
    *,
    interval_s: float,
    duration_s: float,
) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_count = int(math.ceil(duration_s / interval_s)) + 1
    rows_for_hash: List[Tuple[str, str, str, str]] = []

    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["time_s", "x_m", "y_m", "z_m"])
        for sample_idx in range(sample_count):
            time_s = min(sample_idx * interval_s, duration_s)
            x, y = _position_at(waypoints, time_s)
            row = (
                _format_float(time_s),
                _format_float(x),
                _format_float(y),
                _format_float(0.0),
            )
            rows_for_hash.append(row)
            writer.writerow(row)

    return {
        "file": path.name,
        "rows": sample_count,
        "sha256": file_sha256(path),
        "content_sha256": _rows_sha256(rows_for_hash),
    }


def _combined_sha256(manifest: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _generate_run(
    out_dir: Path,
    *,
    run_index: int,
    base_seed: int,
    grid: GridSpec,
    node_count: int,
    coverage_node_count: int,
    interval_s: float,
    duration_s: float,
    coverage_start_s: float,
    post_coverage_window_s: float,
    coverage_speed_mps: float,
    patrol_speed_mps: float,
    accepted_t_cover_range_s: Tuple[float, float],
) -> Dict[str, Any]:
    run_name = "run_{:03d}".format(run_index)
    run_seed = derive_seed(base_seed, "spatial_trace_set", run_index)
    run_rng = random.Random(run_seed)
    run_dir = out_dir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    column_groups = _split_columns(grid.cols, coverage_node_count)
    run_rng.shuffle(column_groups)

    node_entries: List[Dict[str, Any]] = []
    for node_index in range(node_count):
        node_seed = derive_seed(run_seed, "node", node_index)
        node_rng = random.Random(node_seed)
        if node_index < coverage_node_count:
            waypoints = _coverage_waypoints(
                grid,
                column_groups[node_index],
                node_rng=node_rng,
                coverage_start_s=coverage_start_s,
                trace_duration_s=duration_s,
                coverage_speed_mps=coverage_speed_mps,
                patrol_speed_mps=patrol_speed_mps,
            )
            role = "stratified_sweep"
            columns = column_groups[node_index]
        else:
            waypoints = _random_patrol_waypoints(
                grid,
                node_rng,
                duration_s,
                patrol_speed_mps,
            )
            role = "random_patrol"
            columns = []

        entry = _write_trace(
            run_dir / "node_{}.csv".format(node_index),
            waypoints,
            interval_s=interval_s,
            duration_s=duration_s,
        )
        entry.update({
            "node": node_index,
            "seed": int(node_seed),
            "role": role,
        })
        if columns:
            entry["grid_cols"] = list(columns)
        node_entries.append(entry)

    prefix_validations: Dict[str, Dict[str, Any]] = {}
    for prefix in range(coverage_node_count, node_count + 1, coverage_node_count):
        trace_paths = [
            run_dir / "node_{}.csv".format(node_index)
            for node_index in range(prefix)
        ]
        report = check_traces(
            read_traces(trace_paths),
            grid,
            coverage_start_time_s=coverage_start_s,
            post_coverage_window_s=post_coverage_window_s,
        )
        if not report.valid:
            issues = "; ".join(issue.message for issue in report.issues)
            raise ValueError("{} prefix {} failed validation: {}".format(
                run_name, prefix, issues
            ))
        prefix_validations[str(prefix)] = {
            "t_cover_s": report.t_cover_s,
            "covered_cell_count": report.covered_cell_count,
            "total_cell_count": report.total_cell_count,
        }

    first_t_cover = prefix_validations[str(coverage_node_count)]["t_cover_s"]
    if (
        first_t_cover is None
        or first_t_cover < accepted_t_cover_range_s[0]
        or first_t_cover > accepted_t_cover_range_s[1]
    ):
        raise ValueError(
            "{} first {} nodes covered at {}, outside accepted range {}".format(
                run_name,
                coverage_node_count,
                first_t_cover,
                accepted_t_cover_range_s,
            )
        )

    manifest = {
        "policy": TRACE_POLICY,
        "catalog_policy": CATALOG_POLICY,
        "generator_policy": GENERATOR_POLICY,
        "central_seed": int(run_seed),
        "base_seed": int(base_seed),
        "run": run_name,
        "model": GENERATOR_POLICY,
        "area": {
            "x": grid.width_m,
            "y": grid.height_m,
        },
        "grid": {
            "origin_x_m": grid.origin_x_m,
            "origin_y_m": grid.origin_y_m,
            "width_m": grid.width_m,
            "height_m": grid.height_m,
            "rows": grid.rows,
            "cols": grid.cols,
        },
        "interval_s": interval_s,
        "duration_s": duration_s,
        "coverage_start_time_s": coverage_start_s,
        "coverage_speed_mps": coverage_speed_mps,
        "patrol_speed_mps": patrol_speed_mps,
        "accepted_t_cover_range_s": list(accepted_t_cover_range_s),
        "coverage_node_count": coverage_node_count,
        "node_count": node_count,
        "prefix_validations": prefix_validations,
        "nodes": node_entries,
    }
    manifest["combined_sha256"] = _combined_sha256(manifest)

    manifest_path = run_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest_sha256"] = file_sha256(manifest_path)
    _write_json(manifest_path, manifest)
    return {
        "run": run_name,
        "central_seed": int(run_seed),
        "manifest": str(manifest_path.relative_to(out_dir)),
        "manifest_sha256": manifest["manifest_sha256"],
        "combined_sha256": manifest["combined_sha256"],
        "prefix_validations": prefix_validations,
    }


def generate_catalog(args: argparse.Namespace) -> Dict[str, Any]:
    out_dir = Path(args.output)
    if not out_dir.is_absolute():
        out_dir = ROOT_DIR / out_dir
    if out_dir.exists():
        if not args.force:
            raise SystemExit(
                "{} already exists; pass --force to replace it".format(out_dir)
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    grid = GridSpec(
        origin_x_m=0.0,
        origin_y_m=0.0,
        width_m=float(args.area_x_m),
        height_m=float(args.area_y_m),
        rows=int(args.grid_rows),
        cols=int(args.grid_cols),
    )
    if args.node_count % args.coverage_node_count != 0:
        raise SystemExit("node-count must be a multiple of coverage-node-count")
    if grid.cols < args.coverage_node_count:
        raise SystemExit("grid-cols must be >= coverage-node-count")

    accepted_t_cover_range_s = (
        float(args.accepted_t_cover_min_s),
        float(args.accepted_t_cover_max_s),
    )
    coverage_speed_mps = float(args.coverage_speed_mps)
    patrol_speed_mps = float(args.patrol_speed_mps)
    if coverage_speed_mps <= 0.0:
        raise SystemExit("coverage-speed-mps must be positive")
    if patrol_speed_mps <= 0.0:
        raise SystemExit("patrol-speed-mps must be positive")
    if accepted_t_cover_range_s[1] + args.post_coverage_window_s > args.duration_s:
        raise SystemExit("trace duration must include post-coverage window")

    runs = []
    for run_index in range(1, int(args.runs) + 1):
        runs.append(_generate_run(
            out_dir,
            run_index=run_index,
            base_seed=int(args.seed),
            grid=grid,
            node_count=int(args.node_count),
            coverage_node_count=int(args.coverage_node_count),
            interval_s=float(args.interval_s),
            duration_s=float(args.duration_s),
            coverage_start_s=float(args.coverage_start_s),
            post_coverage_window_s=float(args.post_coverage_window_s),
            coverage_speed_mps=coverage_speed_mps,
            patrol_speed_mps=patrol_speed_mps,
            accepted_t_cover_range_s=accepted_t_cover_range_s,
        ))

    catalog = {
        "schema": CATALOG_POLICY,
        "generator_policy": GENERATOR_POLICY,
        "seed": int(args.seed),
        "run_count": int(args.runs),
        "node_count": int(args.node_count),
        "coverage_node_count": int(args.coverage_node_count),
        "density_prefixes": list(range(
            int(args.coverage_node_count),
            int(args.node_count) + 1,
            int(args.coverage_node_count),
        )),
        "area": {
            "x": grid.width_m,
            "y": grid.height_m,
        },
        "grid": {
            "origin_x_m": grid.origin_x_m,
            "origin_y_m": grid.origin_y_m,
            "width_m": grid.width_m,
            "height_m": grid.height_m,
            "rows": grid.rows,
            "cols": grid.cols,
            "cell_count": grid.cell_count,
            "cell_width_m": grid.cell_width,
            "cell_height_m": grid.cell_height,
        },
        "interval_s": float(args.interval_s),
        "duration_s": float(args.duration_s),
        "coverage_start_time_s": float(args.coverage_start_s),
        "post_coverage_window_s": float(args.post_coverage_window_s),
        "coverage_speed_mps": coverage_speed_mps,
        "patrol_speed_mps": patrol_speed_mps,
        "accepted_t_cover_range_s": list(accepted_t_cover_range_s),
        "runs": runs,
    }
    catalog["combined_sha256"] = _combined_sha256(catalog)
    catalog_path = out_dir / "catalog.json"
    _write_json(catalog_path, catalog)
    catalog["catalog_sha256"] = file_sha256(catalog_path)
    _write_json(catalog_path, catalog)
    return catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the spatial grid trace catalog.",
    )
    parser.add_argument("--output", default=str(DEFAULT_CATALOG))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--node-count", type=int, default=50)
    parser.add_argument("--coverage-node-count", type=int, default=10)
    parser.add_argument("--area-x-m", type=float, default=1000.0)
    parser.add_argument("--area-y-m", type=float, default=1000.0)
    parser.add_argument("--grid-rows", type=int, default=24)
    parser.add_argument("--grid-cols", type=int, default=24)
    parser.add_argument("--interval-s", type=float, default=0.2)
    parser.add_argument("--duration-s", type=float, default=220.0)
    parser.add_argument("--coverage-start-s", type=float, default=30.0)
    parser.add_argument("--post-coverage-window-s", type=float, default=10.0)
    parser.add_argument("--coverage-speed-mps", type=float, default=20.0)
    parser.add_argument("--patrol-speed-mps", type=float, default=20.0)
    parser.add_argument("--accepted-t-cover-min-s", type=float, default=120.0)
    parser.add_argument("--accepted-t-cover-max-s", type=float, default=190.0)
    return parser


def main(argv: Sequence[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    catalog = generate_catalog(args)
    print("[OK] Wrote {}".format(Path(args.output)))
    print(
        "[INFO] runs={}, nodes/run={}, grid={}x{}, accepted T_cover={}s".format(
            catalog["run_count"],
            catalog["node_count"],
            catalog["grid"]["rows"],
            catalog["grid"]["cols"],
            catalog["accepted_t_cover_range_s"],
        )
    )
    for run in catalog["runs"]:
        first_prefix = str(catalog["coverage_node_count"])
        print(
            "[INFO] {} first-{} T_cover={:.1f}s".format(
                run["run"],
                catalog["coverage_node_count"],
                run["prefix_validations"][first_prefix]["t_cover_s"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
