#!/usr/bin/env python3
"""Generate plots for spatial-coverage CAR experiments."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


X_LABELS = {
    "density_nodes_km2": "Node density (nodes/km²)",
    "nodes_cfg": "Number of nodes",
    "grid_cell_count": "Number of spatial cells",
    "network_error": "Configured packet error rate",
    "network_range_m": "Radio range (m)",
    "area_km2": "Area (km²)",
    "position_poll_interval_ms": "Position polling interval (ms)",
    "dissemination_interval_s": "Dissemination interval (s)",
    "mobility_speed_max_mps": "Maximum mobility speed (m/s)",
}


def _slug(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_.")
    return text or "unnamed"


def choose_x(df: pd.DataFrame, requested: str = "auto") -> str:
    if requested != "auto":
        if requested not in df.columns:
            raise ValueError(f"x-axis column {requested!r} is not present")
        if pd.to_numeric(df[requested], errors="coerce").notna().sum() == 0:
            raise ValueError(f"x-axis column {requested!r} has no numeric values")
        return requested
    candidates = list(X_LABELS)
    for column in candidates:
        if column in df.columns and pd.to_numeric(df[column], errors="coerce").nunique(dropna=True) > 1:
            return column
    for column in candidates:
        if column in df.columns and pd.to_numeric(df[column], errors="coerce").notna().any():
            return column
    raise ValueError("no numeric scenario parameter is available for an x axis")


def _number(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _save(fig: plt.Figure, base: Path, formats: Iterable[str]) -> List[Path]:
    base.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for extension in formats:
        # ``base`` can legitimately end in a decimal checkpoint such as
        # ``plus_2.5s``; Path.with_suffix() would mistake ``.5s`` for a suffix.
        path = Path(f"{base}.{extension}")
        fig.savefig(path, bbox_inches="tight", dpi=180)
        written.append(path)
    plt.close(fig)
    return written


def _errorbar(ax, frame: pd.DataFrame, x: str, y: str, low: str, high: str, label: str):
    frame = frame.copy()
    frame[x] = _number(frame, x)
    frame[y] = _number(frame, y)
    frame = frame.dropna(subset=[x, y]).sort_values(x)
    if frame.empty:
        return
    lower = np.maximum(0.0, frame[y].to_numpy() - _number(frame, low).to_numpy())
    upper = np.maximum(0.0, _number(frame, high).to_numpy() - frame[y].to_numpy())
    ax.errorbar(
        frame[x], frame[y], yerr=np.vstack([lower, upper]), marker="o",
        capsize=3, linewidth=1.6, label=label,
    )


def plot_checkpoint_car(
    frame: pd.DataFrame, output_dir: Path, requested_x: str, formats: Sequence[str]
) -> List[Path]:
    written = []
    for family, family_df in frame.groupby("scenario_family", dropna=False):
        x = choose_x(family_df, requested_x)
        for offset, offset_df in family_df.groupby("checkpoint_offset_s", dropna=False):
            fig, ax = plt.subplots(figsize=(7.2, 4.6))
            for algorithm, group in offset_df.groupby("algorithm", dropna=False):
                _errorbar(
                    ax, group, x, "swarm_car_mean", "swarm_car_ci_low",
                    "swarm_car_ci_high", str(algorithm),
                )
            ax.set_xlabel(X_LABELS.get(x, x))
            ax.set_ylabel("Swarm coverage accuracy ratio (CAR)")
            ax.set_ylim(-0.02, 1.03)
            ax.grid(True, alpha=0.25)
            ax.legend(title="Algorithm")
            ax.set_title(f"{family}: CAR at T_cover + {float(offset):g} s")
            written.extend(_save(fig, output_dir / f"car_{_slug(family)}_plus_{float(offset):g}s", formats))
    return written


def plot_recovery_curves(frame: pd.DataFrame, output_dir: Path, formats: Sequence[str]) -> List[Path]:
    written = []
    keys = ["scenario_family", "datapoint_key"]
    for (family, datapoint), group_df in frame.groupby(keys, dropna=False):
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        for algorithm, group in group_df.groupby("algorithm", dropna=False):
            _errorbar(
                ax, group, "checkpoint_offset_s", "swarm_car_mean",
                "swarm_car_ci_low", "swarm_car_ci_high", str(algorithm),
            )
        label = str(group_df.get("datapoint_label", pd.Series([datapoint])).iloc[0])
        ax.set_xlabel("Time since T_cover (s)")
        ax.set_ylabel("Swarm coverage accuracy ratio (CAR)")
        ax.set_ylim(-0.02, 1.03)
        ax.grid(True, alpha=0.25)
        ax.legend(title="Algorithm")
        ax.set_title(f"{family}: recovery after physical coverage\n{label}")
        written.extend(_save(fig, output_dir / f"car_recovery_{_slug(family)}_{_slug(datapoint)}", formats))
    return written


def plot_tcover(
    frame: pd.DataFrame, output_dir: Path, requested_x: str, formats: Sequence[str]
) -> List[Path]:
    written = []
    for family, group in frame.groupby("scenario_family", dropna=False):
        x = choose_x(group, requested_x)
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        _errorbar(ax, group, x, "t_cover_s_mean", "t_cover_s_ci_low", "t_cover_s_ci_high", "Mobility traces")
        ax.set_xlabel(X_LABELS.get(x, x))
        ax.set_ylabel("T_cover (s)")
        ax.grid(True, alpha=0.25)
        ax.set_title(f"{family}: physical coverage time")
        written.extend(_save(fig, output_dir / f"tcover_{_slug(family)}", formats))
    return written


def plot_node_distributions(
    frame: pd.DataFrame, output_dir: Path, requested_x: str, formats: Sequence[str]
) -> List[Path]:
    if frame.empty:
        return []
    group_keys = ["scenario_family", "datapoint_key", "algorithm", "run"]
    frame = frame.copy()
    frame["checkpoint_offset_s"] = _number(frame, "checkpoint_offset_s")
    final_offsets = frame.groupby(group_keys, dropna=False)["checkpoint_offset_s"].transform("max")
    frame = frame[frame["checkpoint_offset_s"].eq(final_offsets)]
    frame["node_car"] = _number(frame, "node_car")
    written = []
    for family, group in frame.groupby("scenario_family", dropna=False):
        x = choose_x(group, requested_x)
        group[x] = _number(group, x)
        algorithms = sorted(str(item) for item in group["algorithm"].dropna().unique())
        x_values = sorted(group[x].dropna().unique())
        if not algorithms or not x_values:
            continue
        width = 0.75 / max(1, len(algorithms))
        fig, ax = plt.subplots(figsize=(max(7.2, len(x_values) * 1.1), 4.8))
        legend_handles = []
        colors = plt.get_cmap("tab10")
        for index, algorithm in enumerate(algorithms):
            values = []
            positions = []
            for xpos, xvalue in enumerate(x_values):
                sample = group[(group["algorithm"].astype(str) == algorithm) & group[x].eq(xvalue)]["node_car"].dropna().to_numpy()
                if len(sample):
                    values.append(sample)
                    positions.append(xpos - 0.375 + width / 2 + index * width)
            if not values:
                continue
            box = ax.boxplot(values, positions=positions, widths=width * 0.85, patch_artist=True, manage_ticks=False, showfliers=True)
            color = colors(index % 10)
            for patch in box["boxes"]:
                patch.set_facecolor(color)
                patch.set_alpha(0.55)
            legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=7, alpha=0.55, label=algorithm))
        ax.set_xticks(range(len(x_values)), [f"{value:g}" for value in x_values])
        ax.set_xlabel(X_LABELS.get(x, x))
        ax.set_ylabel("Per-node CAR at final checkpoint")
        ax.set_ylim(-0.02, 1.03)
        ax.grid(True, axis="y", alpha=0.25)
        ax.legend(handles=legend_handles, title="Algorithm")
        ax.set_title(f"{family}: descriptive node distribution (runs remain clustered)")
        written.extend(_save(fig, output_dir / f"car_nodes_final_{_slug(family)}", formats))
    return written


def plot_overhead_tradeoff(frame: pd.DataFrame, output_dir: Path, formats: Sequence[str]) -> List[Path]:
    written = []
    for family, group in frame.groupby("scenario_family", dropna=False):
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        for algorithm, algorithm_df in group.groupby("algorithm", dropna=False):
            x = _number(algorithm_df, "total_packets_mean")
            y = _number(algorithm_df, "final_swarm_car_mean")
            mask = x.notna() & y.notna()
            if not mask.any():
                continue
            selected = algorithm_df.loc[mask]
            x_values = x[mask].to_numpy()
            y_values = y[mask].to_numpy()
            x_low = _number(selected, "total_packets_ci_low").to_numpy()
            x_high = _number(selected, "total_packets_ci_high").to_numpy()
            y_low = _number(selected, "final_swarm_car_ci_low").to_numpy()
            y_high = _number(selected, "final_swarm_car_ci_high").to_numpy()
            xerr = np.vstack(
                [np.maximum(0.0, x_values - x_low), np.maximum(0.0, x_high - x_values)]
            )
            yerr = np.vstack(
                [np.maximum(0.0, y_values - y_low), np.maximum(0.0, y_high - y_values)]
            )
            ax.errorbar(
                x_values, y_values, xerr=xerr, yerr=yerr, fmt="o",
                capsize=3, label=str(algorithm),
            )
        ax.set_xlabel("Total captured packets per run (mean)")
        ax.set_ylabel("Final swarm CAR (mean)")
        ax.set_ylim(-0.02, 1.03)
        ax.grid(True, alpha=0.25)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(title="Algorithm")
        ax.set_title(f"{family}: network cost versus final CAR")
        written.extend(_save(fig, output_dir / f"car_overhead_{_slug(family)}", formats))
    return written


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"required spatial result table does not exist: {path}")
    return pd.read_csv(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("results"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/plots/spatial"))
    parser.add_argument("--x", default="auto", help="numeric scenario column for sweep plots (default: auto)")
    parser.add_argument("--formats", default="png,pdf", help="comma-separated output formats")
    args = parser.parse_args(argv)
    formats = tuple(item.strip().lstrip(".") for item in args.formats.split(",") if item.strip())
    if not formats:
        parser.error("--formats must contain at least one extension")

    car = _read(args.input_dir / "aggregated_spatial_car.csv")
    tcover = _read(args.input_dir / "aggregated_spatial_tcover.csv")
    overhead = _read(args.input_dir / "aggregated_spatial_overhead.csv")
    nodes = _read(args.input_dir / "all_spatial_car_nodes.csv")
    written = []
    written.extend(plot_checkpoint_car(car, args.output_dir, args.x, formats))
    written.extend(plot_recovery_curves(car, args.output_dir, formats))
    written.extend(plot_tcover(tcover, args.output_dir, args.x, formats))
    written.extend(plot_node_distributions(nodes, args.output_dir, args.x, formats))
    written.extend(plot_overhead_tradeoff(overhead, args.output_dir, formats))
    for path in written:
        print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
