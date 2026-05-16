#!/usr/bin/env python3
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classes.mobility.pymobility.models.mobility import reference_point_group


DEFAULTS = {
    "nodes": 30,
    "area_x": 1000.0,
    "area_y": 1000.0,
    "range_m": 160.0,
    "speed_min": 4.0,
    "speed_max": 4.2,
    "duration_s": 300.0,
    "sample_interval_s": 1.0,
    "warmup_s": 30.0,
    "cell_size_m": 50.0,
    "layouts": ["random", "grid", "clustered"],
    "seeds": [1, 2, 3, 4, 5],
    "aggregations": {"low": 0.0, "medium": 6.5, "high": 13.0},
}


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Characterize reference_point_group topology from offline mobility snapshots."
    )
    ap.add_argument("--config", help="Optional scenario/topology JSON config.")
    ap.add_argument("--out-dir", default="results/reference_point_group_topology")
    ap.add_argument("--nodes", type=int)
    ap.add_argument("--area-x", type=float)
    ap.add_argument("--area-y", type=float)
    ap.add_argument("--range-m", type=float)
    ap.add_argument("--speed-min", type=float)
    ap.add_argument("--speed-max", type=float)
    ap.add_argument("--duration-s", type=float)
    ap.add_argument("--sample-interval-s", type=float)
    ap.add_argument("--warmup-s", type=float)
    ap.add_argument("--cell-size-m", type=float)
    ap.add_argument("--layouts", help="Comma-separated layouts: random,grid,clustered.")
    ap.add_argument("--seeds", help="Comma-separated integer seeds.")
    ap.add_argument(
        "--aggregations",
        help="Comma-separated aggregation spec, e.g. low:0,medium:6.5,high:13.",
    )
    ap.add_argument("--no-plots", action="store_true")
    return ap.parse_args()


def _as_float_pair(raw, default_x: float, default_y: float) -> Tuple[float, float]:
    if isinstance(raw, dict):
        return float(raw.get("x", default_x)), float(raw.get("y", default_y))
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return float(raw[0]), float(raw[1])
    return default_x, default_y


def parse_aggregations(spec: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            label, value = token.split(":", 1)
            out[label.strip()] = float(value)
        else:
            value = float(token)
            out[f"agg_{value:g}"] = value
    if not out:
        raise ValueError("aggregation spec is empty")
    return out


def parse_int_list(spec: str) -> List[int]:
    values = [int(x.strip()) for x in spec.split(",") if x.strip()]
    if not values:
        raise ValueError("seed list is empty")
    return values


def parse_str_list(spec: str) -> List[str]:
    values = [x.strip() for x in spec.split(",") if x.strip()]
    if not values:
        raise ValueError("list is empty")
    return values


def load_params(args: argparse.Namespace) -> Dict[str, object]:
    params = dict(DEFAULTS)
    params["layouts"] = list(DEFAULTS["layouts"])
    params["seeds"] = list(DEFAULTS["seeds"])
    params["aggregations"] = dict(DEFAULTS["aggregations"])

    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        area_x, area_y = _as_float_pair(
            cfg.get("simulation", {}).get("area"),
            float(params["area_x"]),
            float(params["area_y"]),
        )
        params["area_x"] = area_x
        params["area_y"] = area_y
        if "duration" in cfg.get("simulation", {}):
            params["duration_s"] = float(cfg["simulation"]["duration"])
        if "count" in cfg.get("nodes", {}):
            params["nodes"] = int(cfg["nodes"]["count"])
        if "range" in cfg.get("network", {}):
            params["range_m"] = float(cfg["network"]["range"])
        mob = cfg.get("mobility", {})
        if "speed" in mob:
            params["speed_min"] = float(mob["speed"][0])
            params["speed_max"] = float(mob["speed"][1])
        topo = cfg.get("topology", {})
        for key in ("duration_s", "sample_interval_s", "warmup_s", "cell_size_m"):
            if key in topo:
                params[key] = float(topo[key])
        if "layouts" in topo:
            params["layouts"] = list(topo["layouts"])
        elif "distribution" in cfg.get("nodes", {}):
            params["layouts"] = [str(cfg["nodes"]["distribution"])]
        if "seeds" in topo:
            params["seeds"] = [int(x) for x in topo["seeds"]]
        elif "seed" in cfg.get("nodes", {}):
            params["seeds"] = [int(cfg["nodes"]["seed"])]
        if "aggregations" in topo:
            params["aggregations"] = {str(k): float(v) for k, v in topo["aggregations"].items()}
        elif "aggregation" in topo:
            raw_aggregation = topo["aggregation"]
            if isinstance(raw_aggregation, dict):
                params["aggregations"] = {str(k): float(v) for k, v in raw_aggregation.items()}
            else:
                params["aggregations"] = {"scenario": float(raw_aggregation)}
        elif "aggregation" in mob:
            params["aggregations"] = {"scenario": float(mob["aggregation"])}

    overrides = {
        "nodes": args.nodes,
        "area_x": args.area_x,
        "area_y": args.area_y,
        "range_m": args.range_m,
        "speed_min": args.speed_min,
        "speed_max": args.speed_max,
        "duration_s": args.duration_s,
        "sample_interval_s": args.sample_interval_s,
        "warmup_s": args.warmup_s,
        "cell_size_m": args.cell_size_m,
    }
    for key, value in overrides.items():
        if value is not None:
            params[key] = value
    if args.layouts is not None:
        params["layouts"] = parse_str_list(args.layouts)
    if args.seeds is not None:
        params["seeds"] = parse_int_list(args.seeds)
    if args.aggregations is not None:
        params["aggregations"] = parse_aggregations(args.aggregations)

    if float(params["sample_interval_s"]) <= 0:
        raise ValueError("sample_interval_s must be positive")
    if float(params["cell_size_m"]) <= 0:
        raise ValueError("cell_size_m must be positive")
    if int(params["nodes"]) <= 0:
        raise ValueError("nodes must be positive")

    return params


def random_positions(n: int, area: Tuple[float, float], rng: np.random.Generator) -> np.ndarray:
    x = rng.uniform(0.0, area[0], size=n)
    y = rng.uniform(0.0, area[1], size=n)
    return np.column_stack((x, y))


def grid_positions(n: int, area: Tuple[float, float], rng: np.random.Generator) -> np.ndarray:
    del rng
    side = int(math.ceil(math.sqrt(n)))
    dx = area[0] / side
    dy = area[1] / side
    pos = []
    for i in range(side):
        for j in range(side):
            if len(pos) == n:
                return np.asarray(pos, dtype=float)
            pos.append(((i + 0.5) * dx, (j + 0.5) * dy))
    return np.asarray(pos, dtype=float)


def clustered_positions(n: int, area: Tuple[float, float], rng: np.random.Generator) -> np.ndarray:
    clusters = max(2, min(4, int(math.ceil(math.sqrt(n) / 2.0))))
    spread = max(1.0, min(area) / 12.0)
    centers = np.column_stack(
        (
            rng.uniform(0.0, area[0], size=clusters),
            rng.uniform(0.0, area[1], size=clusters),
        )
    )
    pos = np.empty((n, 2), dtype=float)
    for i in range(n):
        center = centers[i % clusters]
        pos[i, 0] = np.clip(rng.normal(center[0], spread), 0.0, area[0])
        pos[i, 1] = np.clip(rng.normal(center[1], spread), 0.0, area[1])
    rng.shuffle(pos)
    return pos


def make_initial_positions(layout: str, n: int, area: Tuple[float, float], seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if layout == "random":
        return random_positions(n, area, rng)
    if layout == "grid":
        return grid_positions(n, area, rng)
    if layout == "clustered":
        return clustered_positions(n, area, rng)
    raise ValueError(f"unknown initial layout: {layout}")


def compute_snapshot(
    pos: np.ndarray,
    range_m: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[int], Set[Tuple[int, int]]]:
    n = pos.shape[0]
    degrees = np.zeros(n, dtype=int)
    component_ids = np.zeros(n, dtype=int)
    uf = UnionFind(n)
    edges: Set[Tuple[int, int]] = set()
    r2 = range_m * range_m

    for i in range(n):
        for j in range(i + 1, n):
            dx = float(pos[i, 0] - pos[j, 0])
            dy = float(pos[i, 1] - pos[j, 1])
            if (dx * dx + dy * dy) <= r2:
                degrees[i] += 1
                degrees[j] += 1
                uf.union(i, j)
                edges.add((i, j))

    root_to_id: Dict[int, int] = {}
    comp_sizes: Dict[int, int] = {}
    for i in range(n):
        root = uf.find(i)
        comp_sizes[root] = comp_sizes.get(root, 0) + 1
        if root not in root_to_id:
            root_to_id[root] = len(root_to_id)
        component_ids[i] = root_to_id[root]

    component_size_by_id = [0] * len(root_to_id)
    for root, size in comp_sizes.items():
        component_size_by_id[root_to_id[root]] = size
    component_sizes_per_node = np.array(
        [component_size_by_id[component_ids[i]] for i in range(n)],
        dtype=int,
    )
    return degrees, component_ids, component_sizes_per_node, component_size_by_id, edges


def cell_ids(pos: np.ndarray, area: Tuple[float, float], cell_size_m: float) -> Iterable[Tuple[int, int]]:
    nx = int(math.ceil(area[0] / cell_size_m))
    ny = int(math.ceil(area[1] / cell_size_m))
    x = np.clip(pos[:, 0], 0.0, np.nextafter(area[0], 0.0))
    y = np.clip(pos[:, 1], 0.0, np.nextafter(area[1], 0.0))
    cx = np.clip((x / cell_size_m).astype(int), 0, nx - 1)
    cy = np.clip((y / cell_size_m).astype(int), 0, ny - 1)
    for a, b in zip(cx, cy):
        yield int(a), int(b)


def simulate_condition(
    *,
    aggregation_label: str,
    aggregation: float,
    layout: str,
    seed: int,
    n: int,
    area: Tuple[float, float],
    range_m: float,
    speed: Tuple[float, float],
    duration_s: float,
    sample_interval_s: float,
    cell_size_m: float,
) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    run = f"aggregation={aggregation_label}__layout={layout}__seed={seed}"
    initial = make_initial_positions(layout, n, area, seed)

    np.random.seed(seed)
    mobility = reference_point_group(
        n,
        dimensions=area,
        velocity=speed,
        aggregation=aggregation,
        initial_positions=initial,
    )

    times = np.arange(0.0, duration_s + (sample_interval_s * 0.5), sample_interval_s)
    total_cells = int(math.ceil(area[0] / cell_size_m)) * int(math.ceil(area[1] / cell_size_m))
    visited_cells: Set[Tuple[int, int]] = set()
    active_contacts: Dict[Tuple[int, int], float] = {}

    node_rows: List[dict] = []
    snapshot_rows: List[dict] = []
    component_rows: List[dict] = []
    contact_rows: List[dict] = []

    pos = initial
    for step, t in enumerate(times):
        if step > 0:
            pos = np.asarray(next(mobility), dtype=float)

        degrees, component_ids, component_sizes, component_size_by_id, edges = compute_snapshot(pos, range_m)
        visited_cells.update(cell_ids(pos, area, cell_size_m))

        for pair in sorted(set(active_contacts) - edges):
            start_t = active_contacts.pop(pair)
            contact_rows.append(
                {
                    "run": run,
                    "aggregation_label": aggregation_label,
                    "aggregation": aggregation,
                    "initial_layout": layout,
                    "seed": seed,
                    "node_a": pair[0],
                    "node_b": pair[1],
                    "start_t": start_t,
                    "end_t": float(t),
                    "duration_s": float(t - start_t),
                    "censored": 0,
                }
            )
        for pair in sorted(edges - set(active_contacts)):
            active_contacts[pair] = float(t)

        lcc_size = max(component_size_by_id) if component_size_by_id else 0
        isolated = int((degrees == 0).sum())
        snapshot_rows.append(
            {
                "run": run,
                "aggregation_label": aggregation_label,
                "aggregation": aggregation,
                "initial_layout": layout,
                "seed": seed,
                "time_s": float(t),
                "mean_degree": float(np.mean(degrees)),
                "isolated_ratio": float(isolated / n),
                "lcc_size": int(lcc_size),
                "lcc_ratio": float(lcc_size / n),
                "num_components": int(len(component_size_by_id)),
                "active_contacts": int(len(edges)),
                "coverage_cells": int(len(visited_cells)),
                "coverage_ratio": float(len(visited_cells) / total_cells),
            }
        )

        for component_id, component_size in enumerate(component_size_by_id):
            component_rows.append(
                {
                    "run": run,
                    "aggregation_label": aggregation_label,
                    "aggregation": aggregation,
                    "initial_layout": layout,
                    "seed": seed,
                    "time_s": float(t),
                    "component_id": int(component_id),
                    "component_size": int(component_size),
                }
            )

        for node in range(n):
            node_rows.append(
                {
                    "run": run,
                    "aggregation_label": aggregation_label,
                    "aggregation": aggregation,
                    "initial_layout": layout,
                    "seed": seed,
                    "time_s": float(t),
                    "node": node,
                    "x_m": float(pos[node, 0]),
                    "y_m": float(pos[node, 1]),
                    "degree": int(degrees[node]),
                    "component_id": int(component_ids[node]),
                    "component_size": int(component_sizes[node]),
                    "is_isolated": int(degrees[node] == 0),
                }
            )

    end_t = float(times[-1] + sample_interval_s)
    for pair, start_t in sorted(active_contacts.items()):
        contact_rows.append(
            {
                "run": run,
                "aggregation_label": aggregation_label,
                "aggregation": aggregation,
                "initial_layout": layout,
                "seed": seed,
                "node_a": pair[0],
                "node_b": pair[1],
                "start_t": start_t,
                "end_t": end_t,
                "duration_s": float(end_t - start_t),
                "censored": 1,
            }
        )

    return node_rows, snapshot_rows, component_rows, contact_rows


def ecdf(values: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return np.array([]), np.array([])
    arr.sort()
    y = np.arange(1, arr.size + 1, dtype=float) / arr.size
    return arr, y


def safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)


def _time_step_s(snap_df: pd.DataFrame) -> float:
    times = np.sort(pd.to_numeric(snap_df["time_s"], errors="coerce").dropna().unique())
    if len(times) < 2:
        return 1.0
    diffs = np.diff(times)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 1.0
    return float(np.median(diffs))


def _window_mask(df: pd.DataFrame, start_s: float, end_s: float) -> pd.Series:
    t = pd.to_numeric(df["time_s"], errors="coerce")
    return (t >= start_s) & (t < end_s)


def _coverage_ratio_for_positions(
    node_g: pd.DataFrame,
    area: Tuple[float, float],
    cell_size_m: float,
) -> float:
    if node_g.empty:
        return np.nan
    pos = node_g[["x_m", "y_m"]].to_numpy(dtype=float)
    visited = set(cell_ids(pos, area, cell_size_m))
    total_cells = int(math.ceil(area[0] / cell_size_m)) * int(math.ceil(area[1] / cell_size_m))
    return float(len(visited) / total_cells) if total_cells > 0 else np.nan


def _clip_contacts_to_window(contact_df: pd.DataFrame, start_s: float, end_s: float) -> pd.DataFrame:
    if contact_df.empty:
        return contact_df.copy()

    out = contact_df.copy()
    out["clipped_start_t"] = np.maximum(pd.to_numeric(out["start_t"], errors="coerce"), start_s)
    out["clipped_end_t"] = np.minimum(pd.to_numeric(out["end_t"], errors="coerce"), end_s)
    out["clipped_duration_s"] = out["clipped_end_t"] - out["clipped_start_t"]
    out = out[out["clipped_duration_s"] > 0].copy()
    return out


def _build_run_summary(
    *,
    node_df: pd.DataFrame,
    snap_df: pd.DataFrame,
    comp_df: pd.DataFrame,
    contact_df: pd.DataFrame,
    area: Tuple[float, float],
    cell_size_m: float,
    window_name: str,
    start_s: float,
    end_s: float,
) -> pd.DataFrame:
    snap_window = snap_df[_window_mask(snap_df, start_s, end_s)].copy()
    node_window = node_df[_window_mask(node_df, start_s, end_s)].copy()
    comp_window = comp_df[_window_mask(comp_df, start_s, end_s)].copy()
    contact_window = _clip_contacts_to_window(contact_df, start_s, end_s)

    run_rows = []
    for run, g in snap_window.groupby("run"):
        node_g = node_window[node_window["run"] == run]
        comp_g = comp_window[comp_window["run"] == run]
        contact_g = contact_window[contact_window["run"] == run]
        meta = g.iloc[0]

        all_snap_run = snap_df[snap_df["run"] == run].sort_values("time_s")
        last_total = all_snap_run.iloc[-1] if not all_snap_run.empty else meta
        last_window = g.sort_values("time_s").iloc[-1]

        contact_duration = (
            pd.to_numeric(contact_g["clipped_duration_s"], errors="coerce")
            if "clipped_duration_s" in contact_g.columns
            else pd.Series(dtype=float)
        )

        run_rows.append(
            {
                "analysis_window": window_name,
                "window_start_s": float(start_s),
                "window_end_s": float(end_s),
                "run": run,
                "aggregation_label": meta["aggregation_label"],
                "aggregation": float(meta["aggregation"]),
                "initial_layout": meta["initial_layout"],
                "seed": int(meta["seed"]),
                "mean_degree": float(g["mean_degree"].mean()),
                "degree_median": float(node_g["degree"].median()) if not node_g.empty else np.nan,
                "isolated_ratio": float(g["isolated_ratio"].mean()),
                "lcc_ratio": float(g["lcc_ratio"].mean()),
                "num_components": float(g["num_components"].mean()),
                "component_size_median": float(comp_g["component_size"].median()) if not comp_g.empty else np.nan,
                "window_coverage_ratio": _coverage_ratio_for_positions(node_g, area, cell_size_m),
                "window_end_cumulative_coverage_ratio": float(last_window["coverage_ratio"]),
                "final_cumulative_coverage_ratio": float(last_total["coverage_ratio"]),
                "contact_duration_median_s": float(contact_duration.median()) if not contact_duration.empty else np.nan,
                "contact_duration_mean_s": float(contact_duration.mean()) if not contact_duration.empty else np.nan,
                "contacts_observed": int(len(contact_g)),
                "contacts_censored": int(contact_g["censored"].sum()) if not contact_g.empty else 0,
            }
        )

    return pd.DataFrame(run_rows)


def _aggregate_summary(run_summary: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
    metric_cols = [
        "mean_degree",
        "degree_median",
        "isolated_ratio",
        "lcc_ratio",
        "num_components",
        "component_size_median",
        "window_coverage_ratio",
        "window_end_cumulative_coverage_ratio",
        "final_cumulative_coverage_ratio",
        "contact_duration_median_s",
        "contact_duration_mean_s",
        "contacts_observed",
        "contacts_censored",
    ]
    cols = [c for c in metric_cols if c in run_summary.columns]
    return run_summary.groupby(group_cols, as_index=False)[cols].mean(numeric_only=True)


def _level_series(values: pd.Series, *, higher_is_high: bool = True) -> pd.Series:
    labels = ["low", "medium", "high"]
    vals = pd.to_numeric(values, errors="coerce")
    valid = vals.dropna().sort_values()
    if valid.empty:
        return pd.Series(["unknown"] * len(values), index=values.index)

    ranks = vals.rank(method="dense", ascending=higher_is_high)
    max_rank = int(ranks.max()) if np.isfinite(ranks.max()) else 1
    if max_rank == 1:
        return pd.Series(["similar"] * len(values), index=values.index)

    out = []
    for rank in ranks:
        if not np.isfinite(rank):
            out.append("unknown")
            continue
        idx = int(round((float(rank) - 1.0) * (len(labels) - 1) / max(1, max_rank - 1)))
        out.append(labels[idx])
    return pd.Series(out, index=values.index)


def _add_conclusion_levels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["coverage_level"] = _level_series(out["window_coverage_ratio"], higher_is_high=True)
    out["connectivity_level"] = _level_series(out["lcc_ratio"], higher_is_high=True)
    out["partitioning_level"] = _level_series(out["num_components"], higher_is_high=True)
    out["contact_duration_level"] = _level_series(out["contact_duration_median_s"], higher_is_high=True)
    out["conclusion_hint"] = out.apply(
        lambda r: (
            f"coverage={r['coverage_level']}; "
            f"connectivity={r['connectivity_level']}; "
            f"partitioning={r['partitioning_level']}; "
            f"contacts={r['contact_duration_level']}"
        ),
        axis=1,
    )
    return out


def _write_warmup_effect(out_dir: Path, pre: pd.DataFrame, post: pd.DataFrame) -> None:
    if pre.empty or post.empty:
        return

    keys = ["aggregation_label", "aggregation", "initial_layout"]
    keep_metrics = [
        "mean_degree",
        "isolated_ratio",
        "lcc_ratio",
        "num_components",
        "window_coverage_ratio",
        "contact_duration_median_s",
    ]
    left = pre[keys + keep_metrics].copy()
    right = post[keys + keep_metrics].copy()
    merged = left.merge(right, on=keys, how="inner", suffixes=("_warmup", "_post_warmup"))

    for metric in keep_metrics:
        a = f"{metric}_warmup"
        b = f"{metric}_post_warmup"
        merged[f"{metric}_delta_post_minus_warmup"] = merged[b] - merged[a]
        denom = merged[a].replace(0, np.nan)
        merged[f"{metric}_relative_delta"] = (merged[b] - merged[a]) / denom

    merged.to_csv(out_dir / "warmup_effect_by_condition.csv", index=False)


def plot_outputs(out_dir: Path, warmup_s: float) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    node_df = pd.read_csv(out_dir / "node_snapshots.csv")
    snap_df = pd.read_csv(out_dir / "snapshot_metrics.csv")
    comp_df = pd.read_csv(out_dir / "component_samples.csv")
    contact_df = pd.read_csv(out_dir / "contacts.csv")

    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    agg_labels = list(dict.fromkeys(node_df["aggregation_label"].astype(str).tolist()))
    layouts = list(dict.fromkeys(node_df["initial_layout"].astype(str).tolist()))

    for agg_label in agg_labels:
        agg_safe = safe_name(agg_label)
        fig, ax = plt.subplots(figsize=(5.0, 3.4))
        for layout in layouts:
            vals = node_df[
                (node_df["aggregation_label"] == agg_label)
                & (node_df["initial_layout"] == layout)
                & (node_df["time_s"] >= warmup_s)
            ]["degree"]
            x, y = ecdf(vals)
            if x.size:
                ax.step(x, y, where="post", label=layout)
        ax.set_xlabel("Degree")
        ax.set_ylabel("ECDF")
        ax.grid(True, linestyle=":", alpha=0.55)
        ax.legend()
        fig.tight_layout()
        fig.savefig(plots_dir / f"{agg_safe}_degree_ecdf.pdf")
        fig.savefig(plots_dir / f"{agg_safe}_degree_ecdf.png")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(5.0, 3.4))
        for layout in layouts:
            vals = comp_df[
                (comp_df["aggregation_label"] == agg_label)
                & (comp_df["initial_layout"] == layout)
                & (comp_df["time_s"] >= warmup_s)
            ]["component_size"]
            x, y = ecdf(vals)
            if x.size:
                ax.step(x, y, where="post", label=layout)
        ax.set_xlabel("Component size")
        ax.set_ylabel("ECDF")
        ax.grid(True, linestyle=":", alpha=0.55)
        ax.legend()
        fig.tight_layout()
        fig.savefig(plots_dir / f"{agg_safe}_component_size_ecdf.pdf")
        fig.savefig(plots_dir / f"{agg_safe}_component_size_ecdf.png")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(5.6, 3.4))
        for layout in layouts:
            ts = snap_df[
                (snap_df["aggregation_label"] == agg_label)
                & (snap_df["initial_layout"] == layout)
            ]
            mean_ts = ts.groupby("time_s", as_index=False)["lcc_ratio"].mean()
            if not mean_ts.empty:
                ax.plot(mean_ts["time_s"], mean_ts["lcc_ratio"], label=layout)
        ax.axvline(warmup_s, color="0.35", linestyle="--", linewidth=1.0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Largest component / nodes")
        ax.set_ylim(0.0, 1.02)
        ax.grid(True, linestyle=":", alpha=0.55)
        ax.legend()
        fig.tight_layout()
        fig.savefig(plots_dir / f"{agg_safe}_largest_component_timeseries.pdf")
        fig.savefig(plots_dir / f"{agg_safe}_largest_component_timeseries.png")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(5.6, 3.4))
        for layout in layouts:
            ts = snap_df[
                (snap_df["aggregation_label"] == agg_label)
                & (snap_df["initial_layout"] == layout)
            ]
            mean_ts = ts.groupby("time_s", as_index=False)["coverage_ratio"].mean()
            if not mean_ts.empty:
                ax.plot(mean_ts["time_s"], mean_ts["coverage_ratio"], label=layout)
        ax.axvline(warmup_s, color="0.35", linestyle="--", linewidth=1.0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Visited cells / total cells")
        ax.set_ylim(0.0, 1.02)
        ax.grid(True, linestyle=":", alpha=0.55)
        ax.legend()
        fig.tight_layout()
        fig.savefig(plots_dir / f"{agg_safe}_coverage_timeseries.pdf")
        fig.savefig(plots_dir / f"{agg_safe}_coverage_timeseries.png")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(5.2, 3.4))
        data = [
            contact_df[
                (contact_df["aggregation_label"] == agg_label)
                & (contact_df["initial_layout"] == layout)
                & (contact_df["end_t"] > warmup_s)
            ]["duration_s"].dropna().to_numpy()
            for layout in layouts
        ]
        non_empty = [(layout, vals) for layout, vals in zip(layouts, data) if len(vals) > 0]
        if non_empty:
            labels = [x[0] for x in non_empty]
            values = [x[1] for x in non_empty]
            ax.boxplot(values, labels=labels, showfliers=False)
        ax.set_xlabel("Initial layout")
        ax.set_ylabel("Contact duration (s)")
        ax.grid(True, axis="y", linestyle=":", alpha=0.55)
        fig.tight_layout()
        fig.savefig(plots_dir / f"{agg_safe}_contact_duration_boxplot.pdf")
        fig.savefig(plots_dir / f"{agg_safe}_contact_duration_boxplot.png")
        plt.close(fig)


def summarize(
    out_dir: Path,
    node_df: pd.DataFrame,
    snap_df: pd.DataFrame,
    comp_df: pd.DataFrame,
    contact_df: pd.DataFrame,
    warmup_s: float,
    area: Tuple[float, float],
    cell_size_m: float,
) -> None:
    step_s = _time_step_s(snap_df)
    max_time_s = float(pd.to_numeric(snap_df["time_s"], errors="coerce").max())
    analysis_end_s = max_time_s + step_s

    post_run = _build_run_summary(
        node_df=node_df,
        snap_df=snap_df,
        comp_df=comp_df,
        contact_df=contact_df,
        area=area,
        cell_size_m=cell_size_m,
        window_name="post_warmup",
        start_s=float(warmup_s),
        end_s=analysis_end_s,
    )
    post_run.to_csv(out_dir / "summary_by_run_post_warmup.csv", index=False)
    post_run.to_csv(out_dir / "summary_by_run.csv", index=False)

    post_condition = _aggregate_summary(
        post_run,
        ["analysis_window", "aggregation_label", "aggregation", "initial_layout"],
    ).sort_values(["aggregation", "initial_layout"])
    post_condition.to_csv(out_dir / "summary_by_condition_post_warmup.csv", index=False)
    post_condition.to_csv(out_dir / "summary_by_condition.csv", index=False)

    post_aggregation = _aggregate_summary(
        post_run,
        ["analysis_window", "aggregation_label", "aggregation"],
    ).sort_values("aggregation")
    post_aggregation.to_csv(out_dir / "summary_by_aggregation_post_warmup.csv", index=False)
    post_aggregation.to_csv(out_dir / "summary_by_aggregation.csv", index=False)

    if warmup_s > 0:
        warmup_run = _build_run_summary(
            node_df=node_df,
            snap_df=snap_df,
            comp_df=comp_df,
            contact_df=contact_df,
            area=area,
            cell_size_m=cell_size_m,
            window_name="warmup",
            start_s=0.0,
            end_s=float(warmup_s),
        )
        warmup_run.to_csv(out_dir / "summary_by_run_warmup.csv", index=False)
        warmup_condition = _aggregate_summary(
            warmup_run,
            ["analysis_window", "aggregation_label", "aggregation", "initial_layout"],
        ).sort_values(["aggregation", "initial_layout"])
        warmup_condition.to_csv(out_dir / "summary_by_condition_warmup.csv", index=False)
        warmup_aggregation = _aggregate_summary(
            warmup_run,
            ["analysis_window", "aggregation_label", "aggregation"],
        ).sort_values("aggregation")
        warmup_aggregation.to_csv(out_dir / "summary_by_aggregation_warmup.csv", index=False)
        _write_warmup_effect(out_dir, warmup_condition, post_condition)

    conclusion_by_aggregation = _add_conclusion_levels(
        post_aggregation[
            [
                "analysis_window",
                "aggregation_label",
                "aggregation",
                "window_coverage_ratio",
                "mean_degree",
                "isolated_ratio",
                "lcc_ratio",
                "num_components",
                "contact_duration_median_s",
                "contacts_observed",
            ]
        ].copy()
    )
    conclusion_by_aggregation.to_csv(out_dir / "conclusion_by_aggregation.csv", index=False)

    conclusion_by_condition = (
        post_condition.groupby("initial_layout", group_keys=False)
        .apply(lambda g: _add_conclusion_levels(g.copy()))
        .reset_index(drop=True)
    )
    conclusion_by_condition.to_csv(out_dir / "conclusion_by_condition.csv", index=False)

    conclusion_matrix = conclusion_by_aggregation[
        [
            "aggregation_label",
            "aggregation",
            "window_coverage_ratio",
            "mean_degree",
            "isolated_ratio",
            "lcc_ratio",
            "num_components",
            "contact_duration_median_s",
            "coverage_level",
            "connectivity_level",
            "partitioning_level",
            "contact_duration_level",
            "conclusion_hint",
        ]
    ].copy()
    conclusion_matrix.to_csv(out_dir / "conclusion_matrix.csv", index=False)

    article_table = conclusion_matrix.rename(
        columns={
            "window_coverage_ratio": "post_warmup_coverage_ratio",
            "mean_degree": "post_warmup_mean_degree",
            "isolated_ratio": "post_warmup_isolated_ratio",
            "lcc_ratio": "post_warmup_lcc_ratio",
            "num_components": "post_warmup_num_partitions",
            "contact_duration_median_s": "post_warmup_contact_duration_median_s",
        }
    )
    article_table.to_csv(out_dir / "article_conclusion_table.csv", index=False)


def write_config(out_dir: Path, params: Dict[str, object]) -> None:
    serializable = dict(params)
    serializable["aggregations"] = dict(params["aggregations"])
    serializable["layouts"] = list(params["layouts"])
    serializable["seeds"] = list(params["seeds"])
    (out_dir / "analysis_config.json").write_text(
        json.dumps(serializable, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    params = load_params(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_config(out_dir, params)

    n = int(params["nodes"])
    area = (float(params["area_x"]), float(params["area_y"]))
    range_m = float(params["range_m"])
    speed = (float(params["speed_min"]), float(params["speed_max"]))
    duration_s = float(params["duration_s"])
    sample_interval_s = float(params["sample_interval_s"])
    warmup_s = float(params["warmup_s"])
    cell_size_m = float(params["cell_size_m"])
    layouts = list(params["layouts"])
    seeds = list(params["seeds"])
    aggregations = dict(params["aggregations"])

    all_node_rows: List[dict] = []
    all_snapshot_rows: List[dict] = []
    all_component_rows: List[dict] = []
    all_contact_rows: List[dict] = []

    total = len(aggregations) * len(layouts) * len(seeds)
    done = 0
    for aggregation_label, aggregation in aggregations.items():
        for layout in layouts:
            for seed in seeds:
                done += 1
                print(
                    f"[INFO] Simulating {done}/{total}: "
                    f"aggregation={aggregation_label}({aggregation}), layout={layout}, seed={seed}"
                )
                node_rows, snapshot_rows, component_rows, contact_rows = simulate_condition(
                    aggregation_label=str(aggregation_label),
                    aggregation=float(aggregation),
                    layout=str(layout),
                    seed=int(seed),
                    n=n,
                    area=area,
                    range_m=range_m,
                    speed=speed,
                    duration_s=duration_s,
                    sample_interval_s=sample_interval_s,
                    cell_size_m=cell_size_m,
                )
                all_node_rows.extend(node_rows)
                all_snapshot_rows.extend(snapshot_rows)
                all_component_rows.extend(component_rows)
                all_contact_rows.extend(contact_rows)

    node_df = pd.DataFrame(all_node_rows)
    snap_df = pd.DataFrame(all_snapshot_rows)
    comp_df = pd.DataFrame(all_component_rows)
    contact_df = pd.DataFrame(all_contact_rows)

    node_df.to_csv(out_dir / "node_snapshots.csv", index=False)
    snap_df.to_csv(out_dir / "snapshot_metrics.csv", index=False)
    comp_df.to_csv(out_dir / "component_samples.csv", index=False)
    contact_df.to_csv(out_dir / "contacts.csv", index=False)

    summarize(out_dir, node_df, snap_df, comp_df, contact_df, warmup_s, area, cell_size_m)

    if not args.no_plots:
        plot_outputs(out_dir, warmup_s)

    print(f"[OK] Wrote topology characterization to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
