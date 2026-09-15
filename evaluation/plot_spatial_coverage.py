#!/usr/bin/env python3
"""Generate old-style convergence and network-cost plots for spatial runs."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.ticker import ScalarFormatter


FIGSIZE = (4.2, 4.0)
DPI = 300
FONT_SCALE = 1.2

STYLE_RC = {
    "font.family": "serif",
    "font.serif": ["CMU Serif", "Computer Modern Roman", "DejaVu Serif"],
    "mathtext.fontset": "cm",
    "axes.unicode_minus": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "font.size": 10.5 * FONT_SCALE,
    "axes.labelsize": 10.5 * FONT_SCALE,
    "axes.titlesize": 11.0 * FONT_SCALE,
    "xtick.labelsize": 9.5 * FONT_SCALE,
    "ytick.labelsize": 9.5 * FONT_SCALE,
    "legend.fontsize": 9.0 * FONT_SCALE,
}

sns.set_theme(style="whitegrid", context="paper", rc=STYLE_RC)
plt.rcParams.update(STYLE_RC)

PALETTE = {
    "broadcast": "#1f77b4",
    "rapid": "#9467bd",
    "multiunicast": "#2ca02c",
    "trickle": "#d62728",
    "usfdx3": "#111111",
    "usfdx1": "#17becf",
}

LINESTYLES = {
    "multiunicast": "--",
    "broadcast": "-",
    "rapid": "-.",
    "trickle": ":",
    "usfdx3": (0, (3, 1, 1, 1)),
    "usfdx1": (0, (5, 2)),
}

MARKERS = {
    "multiunicast": "s",
    "broadcast": "o",
    "rapid": "^",
    "trickle": "D",
    "usfdx3": "P",
    "usfdx1": "X",
}

HATCHES = {
    "multiunicast": "///",
    "broadcast": "\\\\",
    "rapid": "xx",
    "trickle": "..",
    "usfdx3": "--",
    "usfdx1": "++",
}

LEGEND_LABELS_SHORT = {
    "multiunicast": "Best-effort Multicast",
    "broadcast": "Flooding Multicast",
    "rapid": "Gossip Multicast",
    "trickle": "Trickle",
    "usfdx3": "USFD-3x",
    "usfdx1": "USFD-1x",
}

X_LABELS = {
    "density_nodes_km2": "Nodes per km$^2$",
    "nodes_cfg": "Number of Nodes",
    "grid_cell_count": "Number of Spatial Cells",
    "network_error": "Configured Packet Error Rate",
    "network_range_m": "Radio Range (m)",
    "area_km2": "Area (km$^2$)",
    "position_poll_interval_ms": "Position Polling Interval (ms)",
    "dissemination_interval_s": "Dissemination Interval (s)",
    "mobility_speed_max_mps": "Maximum Mobility Speed (m/s)",
}


def _slug(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_.")
    return text or "unnamed"


def _offset_slug(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _slug(value)
    if math.isfinite(numeric) and abs(numeric - round(numeric)) < 1e-9:
        return f"{int(round(numeric))}s"
    return f"{numeric:g}s"


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


def _has_cols(df: pd.DataFrame, cols: Sequence[str]) -> bool:
    return all(col in df.columns for col in cols)


def _save(fig: plt.Figure, base: Path, formats: Iterable[str]) -> List[Path]:
    base.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for extension in formats:
        path = Path(f"{base}.{extension}")
        fig.savefig(path, bbox_inches="tight", dpi=DPI)
        written.append(path)
    plt.close(fig)
    return written


def _format_xticks(values: Sequence[object]) -> List[str]:
    labels = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            labels.append(str(value))
            continue
        if abs(numeric - round(numeric)) < 1e-9:
            labels.append(str(int(round(numeric))))
        else:
            labels.append(f"{numeric:.6g}")
    return labels


def _legend_fontsize_pt() -> float:
    base = plt.rcParams.get("legend.fontsize", 10.0)
    try:
        base = float(base)
    except Exception:
        base = 10.0
    return base * 0.88


def _algo_order(df: pd.DataFrame, algo_col: str) -> List[str]:
    desired_order = ("multiunicast", "broadcast", "usfdx1", "usfdx3", "rapid", "trickle")
    present = {str(item) for item in df[algo_col].dropna().unique()}
    order = [algorithm for algorithm in desired_order if algorithm in present]
    if not order:
        order = [str(item) for item in df[algo_col].dropna().unique()]
    return order


def _new_fig_ax():
    return plt.subplots(figsize=FIGSIZE)


def _finalize_layout(fig: plt.Figure) -> None:
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.16, top=0.96)


def _apply_percent_axis(ax) -> None:
    ax.set_ylim(0.0, 1.0)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0, decimals=0))


def _apply_thousands_sci(ax) -> None:
    formatter = ScalarFormatter(useMathText=True)
    formatter.set_scientific(True)
    formatter.set_powerlimits((3, 3))
    ax.yaxis.set_major_formatter(formatter)
    ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=True)
    offset = ax.yaxis.get_offset_text()
    offset.set_x(-0.02)
    offset.set_y(1.01)


def _apply_line_legend_compact(ax, algo_order: Sequence[str], *, prefer: str = "lower") -> None:
    if prefer == "upper":
        loc = "upper left"
        anchor = (0.02, 0.98)
    else:
        loc = "lower left"
        anchor = (0.02, 0.02)

    handles = []
    labels = []
    for algorithm in algo_order:
        handles.append(Line2D(
            [0], [0],
            color=PALETTE.get(algorithm, "gray"),
            linestyle=LINESTYLES.get(algorithm, "-"),
            marker=MARKERS.get(algorithm, "o"),
            markersize=5.2,
            markeredgewidth=0.85,
            markeredgecolor="black",
            markerfacecolor=PALETTE.get(algorithm, "gray"),
            linewidth=2.0,
        ))
        labels.append(LEGEND_LABELS_SHORT.get(algorithm, algorithm))

    ax.legend(
        handles,
        labels,
        loc=loc,
        bbox_to_anchor=anchor,
        frameon=True,
        framealpha=0.92,
        borderaxespad=0.0,
        handlelength=1.8,
        handletextpad=0.6,
        labelspacing=0.25,
        fontsize=_legend_fontsize_pt(),
    )


def _apply_bar_legend_auto(ax, algo_order: Sequence[str], *, prefer: str = "upper") -> None:
    patches = [patch for patch in ax.patches if hasattr(patch, "get_x") and hasattr(patch, "get_height")]
    x0, x1 = ax.get_xlim()
    mid = 0.5 * (x0 + x1)
    left_heights = []
    right_heights = []
    for patch in patches:
        center = patch.get_x() + 0.5 * patch.get_width()
        height = float(patch.get_height())
        if center <= mid:
            left_heights.append(height)
        else:
            right_heights.append(height)

    left_score = float(np.nanmean(left_heights)) if left_heights else 0.0
    right_score = float(np.nanmean(right_heights)) if right_heights else 0.0
    if left_score >= right_score:
        loc = "upper right" if prefer == "upper" else "lower right"
        anchor = (0.98, 0.98) if prefer == "upper" else (0.98, 0.02)
    else:
        loc = "upper left" if prefer == "upper" else "lower left"
        anchor = (0.02, 0.98) if prefer == "upper" else (0.02, 0.02)

    handles = []
    labels = []
    for algorithm in algo_order:
        handles.append(Line2D(
            [0], [0],
            color=PALETTE.get(algorithm, "gray"),
            linestyle="none",
            marker="s",
            markersize=7.0,
            markeredgewidth=0.85,
            markeredgecolor="black",
            markerfacecolor=PALETTE.get(algorithm, "gray"),
        ))
        labels.append(LEGEND_LABELS_SHORT.get(algorithm, algorithm))

    ax.legend(
        handles,
        labels,
        loc=loc,
        bbox_to_anchor=anchor,
        frameon=True,
        framealpha=0.92,
        borderaxespad=0.0,
        handlelength=0.8,
        handletextpad=0.6,
        labelspacing=0.25,
        fontsize=_legend_fontsize_pt(),
    )


def _checkpoint_label(offset: object) -> str:
    try:
        numeric = float(offset)
    except (TypeError, ValueError):
        return str(offset)
    if abs(numeric) < 1e-9:
        return "$T_{cover}$"
    return f"$T_{{cover}} + {numeric:g}$ s"


def _apply_zoom_percent_axis(ax, values: Sequence[float]) -> None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        _apply_percent_axis(ax)
        return
    ymin = max(0.0, min(finite))
    ymax = min(1.0, max(finite))
    span = ymax - ymin
    pad = max(0.01, span * 0.12)
    if span < 0.02:
        center = 0.5 * (ymin + ymax)
        ymin = center - 0.02
        ymax = center + 0.02
    else:
        ymin -= pad
        ymax += pad
    ymin = max(0.0, ymin)
    ymax = min(1.0, ymax)
    if ymax - ymin < 0.03:
        extra = 0.5 * (0.03 - (ymax - ymin))
        ymin = max(0.0, ymin - extra)
        ymax = min(1.0, ymax + extra)
    ax.set_ylim(ymin, ymax)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0, decimals=0))


def _fixed_bar_y_upper(df: pd.DataFrame, mean_col: str, ci_high_col: str) -> Optional[float]:
    values = pd.concat([_number(df, mean_col), _number(df, ci_high_col)], ignore_index=True)
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return None
    top = float(values.max())
    if top <= 0.0:
        return None

    padded = top * 1.005
    step = 10 ** max(0, math.floor(math.log10(padded)) - 2)
    return math.ceil(padded / step) * step


def build_line_with_ci(
    df: pd.DataFrame,
    x: str,
    y: str,
    ci_low: str,
    ci_high: str,
    algo_col: str,
    xlabel: str,
    ylabel: str,
    *,
    zoom_y: bool = False,
    page_label: Optional[str] = None,
) -> Optional[plt.Figure]:
    fig, ax = _new_fig_ax()
    algo_order = _algo_order(df, algo_col)
    plotted = 0
    axis_values: List[float] = []

    for algorithm in algo_order:
        subset = df[df[algo_col].astype(str) == algorithm].copy()
        subset[x] = _number(subset, x)
        subset[y] = _number(subset, y)
        subset = subset.dropna(subset=[x, y]).sort_values(x)
        if subset.empty:
            continue

        xs = subset[x].to_numpy()
        ys = subset[y].to_numpy()
        lo = _number(subset, ci_low).fillna(subset[y]).to_numpy()
        hi = _number(subset, ci_high).fillna(subset[y]).to_numpy()
        axis_values.extend(float(value) for value in ys if math.isfinite(float(value)))
        axis_values.extend(float(value) for value in lo if math.isfinite(float(value)))
        axis_values.extend(float(value) for value in hi if math.isfinite(float(value)))
        color = PALETTE.get(algorithm, "gray")
        ax.plot(
            xs,
            ys,
            linewidth=2.0,
            linestyle=LINESTYLES.get(algorithm, "-"),
            color=color,
            marker=MARKERS.get(algorithm, "o"),
            markersize=4.8,
            markeredgewidth=0.85,
            markeredgecolor="black",
        )
        ax.fill_between(xs, lo, hi, alpha=0.14, color=color)
        plotted += 1

    if plotted == 0:
        plt.close(fig)
        return None

    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    ax.set_xticks(x_values)
    ax.set_xticklabels(_format_xticks(x_values))
    ax.set_xlabel(xlabel, labelpad=6)
    ax.set_ylabel(ylabel, labelpad=6)
    _apply_line_legend_compact(ax, algo_order, prefer="lower")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)
    if zoom_y:
        _apply_zoom_percent_axis(ax, axis_values)
    else:
        _apply_percent_axis(ax)
    if page_label:
        ax.set_title(page_label, pad=8)
    _finalize_layout(fig)
    return fig


def build_bar_with_ci(
    df: pd.DataFrame,
    x: str,
    y: str,
    ci_low: str,
    ci_high: str,
    algo_col: str,
    xlabel: str,
    ylabel: str,
    *,
    page_label: Optional[str] = None,
    y_upper: Optional[float] = None,
) -> Optional[plt.Figure]:
    fig, ax = _new_fig_ax()
    algo_order = _algo_order(df, algo_col)
    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    x_pos = np.arange(len(x_values))
    if len(algo_order) <= 1:
        width = 0.58
    elif len(algo_order) == 2:
        width = 0.36
    else:
        width = 0.26
    plotted = 0

    for index, algorithm in enumerate(algo_order):
        subset = df[df[algo_col].astype(str) == algorithm].copy()
        subset = subset.sort_values(x)
        metrics = pd.DataFrame({x: x_values}).merge(subset, on=x, how="left")
        means = pd.to_numeric(metrics[y], errors="coerce")
        low = pd.to_numeric(metrics[ci_low], errors="coerce").fillna(means)
        high = pd.to_numeric(metrics[ci_high], errors="coerce").fillna(means)
        mask = means.notna()
        if len(algo_order) == 1:
            positions = x_pos
        elif len(algo_order) == 2:
            positions = x_pos - width / 2 if index == 0 else x_pos + width / 2
        else:
            offsets = np.linspace(-width, width, len(algo_order))
            positions = x_pos + offsets[index]

        lower_error = (means - low).where(mask, 0.0)
        upper_error = (high - means).where(mask, 0.0)
        yerr = np.vstack([lower_error.to_numpy(), upper_error.to_numpy()])
        color = PALETTE.get(algorithm, "gray")
        ax.bar(
            positions[mask.to_numpy()],
            means[mask].to_numpy(),
            width,
            color=color,
            edgecolor="black",
            linewidth=0.85,
            hatch=HATCHES.get(algorithm, ""),
            alpha=0.98,
            yerr=yerr[:, mask.to_numpy()],
            capsize=4,
            error_kw={"ecolor": "black", "alpha": 0.9, "linewidth": 1.0},
        )
        plotted += int(mask.sum())

    if plotted == 0:
        plt.close(fig)
        return None

    ax.set_xticks(x_pos)
    ax.set_xticklabels(_format_xticks(x_values))
    ax.set_xlabel(xlabel, labelpad=6)
    ax.set_ylabel(ylabel, labelpad=6)
    _apply_bar_legend_auto(ax, algo_order, prefer="upper")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)
    if y_upper is not None:
        ax.set_ylim(0.0, y_upper)
    _apply_thousands_sci(ax)
    if page_label:
        ax.set_title(page_label, pad=8)
    _finalize_layout(fig)
    return fig


def _write_pdf_pages(path: Path, figures: Sequence[plt.Figure]) -> Optional[Path]:
    if not figures:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        for figure in figures:
            pdf.savefig(figure, bbox_inches="tight", dpi=DPI)
            plt.close(figure)
    return path


def plot_convergence_pdfs(frame: pd.DataFrame, output_dir: Path, requested_x: str) -> List[Path]:
    written = []
    required = ("swarm_car_mean", "swarm_car_ci_low", "swarm_car_ci_high")
    if not _has_cols(frame, required):
        print("[WARN] Missing swarm_car_* columns; skipping spatial convergence plots.")
        return written

    for family, family_df in frame.groupby("scenario_family", dropna=False):
        x = choose_x(family_df, requested_x)
        xlabel = X_LABELS.get(x, x)
        normal_figures = []
        zoom_figures = []
        offset_groups = sorted(
            family_df.groupby("checkpoint_offset_s", dropna=False),
            key=lambda item: float(item[0]),
        )
        for offset, offset_df in offset_groups:
            offset_df = offset_df.copy()
            offset_df[x] = _number(offset_df, x)
            offset_df = offset_df.dropna(subset=[x])
            if offset_df.empty:
                continue
            normal = build_line_with_ci(
                df=offset_df,
                x=x,
                y="swarm_car_mean",
                ci_low="swarm_car_ci_low",
                ci_high="swarm_car_ci_high",
                algo_col="algorithm",
                xlabel=xlabel,
                ylabel="Synchronization (%)",
                zoom_y=False,
                page_label=_checkpoint_label(offset),
            )
            zoom = build_line_with_ci(
                df=offset_df,
                x=x,
                y="swarm_car_mean",
                ci_low="swarm_car_ci_low",
                ci_high="swarm_car_ci_high",
                algo_col="algorithm",
                xlabel=xlabel,
                ylabel="Synchronization (%)",
                zoom_y=True,
                page_label=_checkpoint_label(offset),
            )
            if normal is not None:
                normal_figures.append(normal)
            if zoom is not None:
                zoom_figures.append(zoom)
        normal_path = _write_pdf_pages(output_dir / f"{_slug(family)}__convergence.pdf", normal_figures)
        zoom_path = _write_pdf_pages(output_dir / f"{_slug(family)}__convergence_zoom_y.pdf", zoom_figures)
        if normal_path is not None:
            written.append(normal_path)
        if zoom_path is not None:
            written.append(zoom_path)
    return written


def plot_usage_pdf(
    frame: pd.DataFrame, output_dir: Path, requested_x: str
) -> List[Path]:
    written = []
    required = ("total_packets_mean", "total_packets_ci_low", "total_packets_ci_high")
    if not _has_cols(frame, required):
        print("[WARN] Missing total_packets_* columns; skipping spatial usage plot.")
        return written

    for family, family_df in frame.groupby("scenario_family", dropna=False):
        x = choose_x(family_df, requested_x)
        family_df = family_df.copy()
        family_df[x] = _number(family_df, x)
        family_df = family_df.dropna(subset=[x])
        if family_df.empty:
            continue
        figures = []
        y_upper = _fixed_bar_y_upper(family_df, "total_packets_mean", "total_packets_ci_high")
        if "checkpoint_offset_s" in family_df.columns:
            groups = sorted(
                family_df.groupby("checkpoint_offset_s", dropna=False),
                key=lambda item: float(item[0]),
            )
        else:
            groups = [(None, family_df)]
        for offset, offset_df in groups:
            figure = build_bar_with_ci(
                df=offset_df,
                x=x,
                y="total_packets_mean",
                ci_low="total_packets_ci_low",
                ci_high="total_packets_ci_high",
                algo_col="algorithm",
                xlabel=X_LABELS.get(x, x),
                ylabel="Total Packets",
                page_label=_checkpoint_label(offset) if offset is not None else None,
                y_upper=y_upper,
            )
            if figure is not None:
                figures.append(figure)
        path = _write_pdf_pages(output_dir / f"{_slug(family)}__usage.pdf", figures)
        if path is not None:
            written.append(path)
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
    parser.add_argument("--formats", default="pdf", help="kept for compatibility; spatial plots are multi-page PDFs")
    args = parser.parse_args(argv)

    formats = tuple(item.strip().lstrip(".") for item in args.formats.split(",") if item.strip())
    if not formats or formats != ("pdf",):
        parser.error("spatial checkpoint plots are multi-page PDFs; use --formats pdf")

    car = _read(args.input_dir / "aggregated_spatial_car.csv")
    usage = _read(args.input_dir / "aggregated_spatial_usage_checkpoints.csv")
    written = []
    written.extend(plot_convergence_pdfs(car, args.output_dir, args.x))
    written.extend(plot_usage_pdf(usage, args.output_dir, args.x))
    for path in written:
        print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
