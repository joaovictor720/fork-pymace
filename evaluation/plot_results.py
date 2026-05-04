import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.ticker import ScalarFormatter
import seaborn as sns
from matplotlib.lines import Line2D

INPUT_CSV = "results/aggregated_results.csv"
JOBS_JSON = "evaluation/jobs.json"

OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FORMATS = ("pdf",)

# ==========================
# FIGURE SIZING (PHYSICAL) vs FONT SIZING (POINTS)
# ==========================
FIGSIZE = (4.2, 4.0)  # square-ish for side-by-side layouts
DPI = 300

FONT_SCALE = 1.2  # increases pt sizes; does NOT change figsize

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
    "legend.fontsize": 9.0 * FONT_SCALE,  # slightly smaller to fit better
}

sns.set_theme(style="whitegrid", context="paper", rc=STYLE_RC)
plt.rcParams.update(STYLE_RC)

# No orange
PALETTE = {
    "broadcast": "#1f77b4",   # blue
    "rapid": "#9467bd",       # purple
    "multiunicast": "#2ca02c", # green
    "trickle": "#d62728"      # red
}

LINESTYLES = {
    "multiunicast": "--",
    "broadcast": "-",
    "rapid": "-.",
    "trickle": ":",
}

MARKERS = {
    "multiunicast": "s",
    "broadcast": "o",
    "rapid": "^",
    "trickle": "D",
}

HATCHES = {
    "multiunicast": "///",
    "broadcast": "\\\\",
    "rapid": "xx",
    "trickle": "..",
}

LABELS_ALGO = {
    "broadcast": "Flooding Multicast",
    "multiunicast": "Best-effort Multicast",
    "rapid": "Gossip-based Multicast",
    "trickle": "Trickle",
}

Y_LABELS = {
    "cap": "Synchronization (%)",
    "pkt_total": "Total Packets",
}

COLS = {
    "scenario": "scenario",
    "algorithm": "algorithm",
    "pkt_total_mean": "pkt_total_mean",
    "pkt_total_ci_low": "pkt_total_ci_low",
    "pkt_total_ci_high": "pkt_total_ci_high",
    "cap_node_mean": "cap_node_mean",
    "cap_node_ci_low": "cap_node_ci_low",
    "cap_node_ci_high": "cap_node_ci_high",
}

# ==========================
# Legend tuning knobs (ONLY legend, not global rc)
# ==========================
LEGEND_FONT_SCALE = 0.88   # relative to rcParams legend.fontsize
LEGEND_LABELS_SHORT = {
    "multiunicast": "Best-effort Multicast",
    "broadcast": "Flooding",
    "rapid": "RAPID",
    "trickle": "Trickle",
}

def _legend_fontsize_pt():
    base = plt.rcParams.get("legend.fontsize", 10.0)
    try:
        base = float(base)
    except Exception:
        base = 10.0
    return base * LEGEND_FONT_SCALE

# ==========================
# HELPERS
# ==========================
def _read_jobs_prefixes(path: str):
    p = Path(path)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    jobs = cfg.get("jobs", [])
    prefixes = set()
    for j in jobs:
        sc = str(j.get("scenario", "")).strip()
        if not sc:
            continue
        if sc.endswith("_batman"):
            prefixes.add(sc[:-len("_batman")])
        elif sc.endswith("_ip"):
            prefixes.add(sc[:-len("_ip")])
        else:
            prefixes.add(sc)
    return sorted(prefixes)

def _numeric(df: pd.DataFrame, col: str):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def _has_cols(df: pd.DataFrame, cols):
    return all(c in df.columns for c in cols)

def _scenario_prefix(s: str):
    s = str(s)
    if s.endswith("_batman"):
        return s[:-len("_batman")]
    if s.endswith("_ip"):
        return s[:-len("_ip")]
    return s

def _save_figure(fig, base_filename_no_ext: str):
    for ext in OUTPUT_FORMATS:
        out_path = OUTPUT_DIR / f"{base_filename_no_ext}.{ext}"
        fig.savefig(out_path, bbox_inches="tight", dpi=DPI)
        print(f"[OK] Saved: {out_path}")

def _apply_percent_axis(ax):
    ax.set_ylim(0.0, 1.0)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0, decimals=0))

def _apply_thousands_sci(ax):
    fmt = ScalarFormatter(useMathText=True)
    fmt.set_scientific(True)
    fmt.set_powerlimits((3, 3))
    ax.yaxis.set_major_formatter(fmt)
    ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=True)
    off = ax.yaxis.get_offset_text()
    off.set_x(-0.02)
    off.set_y(1.01)

def _format_xticks(vals):
    out = []
    for v in vals:
        try:
            fv = float(v)
            if abs(fv - round(fv)) < 1e-9:
                out.append(str(int(round(fv))))
            else:
                out.append(f"{fv:.6g}")
        except Exception:
            out.append(str(v))
    return out

def _xlabel_for(prefix: str):
    if prefix in {"density", "area"}:
        return "Nodes per km$^2$"
    if prefix == "scalability":
        return "Number of Nodes"
    return "X"

def _xcol_for(prefix: str, df_prefix: pd.DataFrame):
    if prefix in {"density", "area"}:
        return "density" if "density" in df_prefix.columns else "nodes"
    if prefix == "scalability":
        return "nodes"
    for c in ["density", "nodes", "ops_per_sec", "diss_per_sec", "duration", "cooldown", "error"]:
        if c in df_prefix.columns and pd.to_numeric(df_prefix[c], errors="coerce").dropna().nunique() >= 2:
            return c
    return "nodes"

def _new_fig_ax():
    fig, ax = plt.subplots(figsize=FIGSIZE)
    return fig, ax

def _finalize_layout(fig):
    # Legend is inside; keep plot compact.
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.16, top=0.96)

def _algo_order(df: pd.DataFrame, algo_col: str):
    desired_order = ("multiunicast", "broadcast", "rapid", "trickle")
    present = set(df[algo_col].dropna().unique())
    algo_order = [a for a in desired_order if a in present]
    if not algo_order:
        algo_order = list(df[algo_col].dropna().unique())
    return algo_order

# ==========================
# LEGENDS
# ==========================
def _apply_line_legend_compact(ax, algo_order, *, prefer="upper"):
    """
    For line plots: no bars to inspect. Place legend in a corner.
    Uses short labels to avoid width growth.
    """
    fs = _legend_fontsize_pt()

    # Pick a stable corner.
    if prefer == "upper":
        loc = "upper left"
        anchor = (0.02, 0.98)
    else:
        loc = "lower left"
        anchor = (0.02, 0.02)

    handles = []
    labels = []
    for algo in algo_order:
        handles.append(Line2D(
            [0], [0],
            color=PALETTE.get(algo, "gray"),
            linestyle=LINESTYLES.get(algo, "-"),
            marker=MARKERS.get(algo, "o"),
            markersize=5.2,
            markeredgewidth=0.85,
            markeredgecolor="black",
            markerfacecolor=PALETTE.get(algo, "gray"),
            linewidth=2.0,
        ))
        labels.append(LEGEND_LABELS_SHORT.get(algo, algo))

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
        fontsize=fs,
    )

def _apply_bar_legend_simple(ax, algo_order, *, prefer="upper"):
    """
    Non-recursive fallback for bar plots.
    """
    fs = _legend_fontsize_pt()

    if prefer == "upper":
        loc = "upper left"
        anchor = (0.02, 0.98)
    else:
        loc = "lower left"
        anchor = (0.02, 0.02)

    handles = []
    labels = []
    for algo in algo_order:
        handles.append(Line2D(
            [0], [0],
            color=PALETTE.get(algo, "gray"),
            linestyle="none",
            marker="s",
            markersize=7.0,
            markeredgewidth=0.85,
            markeredgecolor="black",
            markerfacecolor=PALETTE.get(algo, "gray"),
        ))
        labels.append(LEGEND_LABELS_SHORT.get(algo, algo))

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
        fontsize=fs,
    )

def _apply_bar_legend_auto(ax, algo_order, *, prefer="upper"):
    """
    Bar-plot only.
    Picks a corner to avoid covering the most "busy" bar region.
    """
    fs = _legend_fontsize_pt()

    patches = [p for p in ax.patches if hasattr(p, "get_x") and hasattr(p, "get_height")]
    if not patches:
        _apply_bar_legend_simple(ax, algo_order, prefer=prefer)
        return

    x0, x1 = ax.get_xlim()
    mid = 0.5 * (x0 + x1)

    left_heights = []
    right_heights = []
    for p in patches:
        cx = p.get_x() + 0.5 * p.get_width()
        h = float(p.get_height())
        if cx <= mid:
            left_heights.append(h)
        else:
            right_heights.append(h)

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
    for algo in algo_order:
        handles.append(Line2D(
            [0], [0],
            color=PALETTE.get(algo, "gray"),
            linestyle="none",
            marker="s",
            markersize=7.0,
            markeredgewidth=0.85,
            markeredgecolor="black",
            markerfacecolor=PALETTE.get(algo, "gray"),
        ))
        labels.append(LEGEND_LABELS_SHORT.get(algo, algo))

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
        fontsize=fs,
    )

# ==========================
# PLOTS
# ==========================
def plot_bar_with_ci(
    df: pd.DataFrame,
    x: str,
    y: str,
    ci_low: str,
    ci_high: str,
    algo_col: str,
    xlabel: str,
    ylabel: str,
    base_filename_no_ext: str,
    *,
    y_axis_mode: str = "plain",
):
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

    for i, algo in enumerate(algo_order):
        subset = df[df[algo_col] == algo].copy()
        subset = subset.sort_values(x)
        metrics = pd.DataFrame({x: x_values}).merge(subset, on=x, how="left")

        means = pd.to_numeric(metrics[y], errors="coerce")
        lo = pd.to_numeric(metrics[ci_low], errors="coerce")
        hi = pd.to_numeric(metrics[ci_high], errors="coerce")

        mask = means.notna()
        means = means.where(mask)
        lo = lo.where(mask, means)
        hi = hi.where(mask, means)

        yerr_low = (means - lo).where(mask, 0.0)
        yerr_high = (hi - means).where(mask, 0.0)
        yerr = np.vstack([yerr_low.to_numpy(), yerr_high.to_numpy()])

        if len(algo_order) == 1:
            pos = x_pos
        elif len(algo_order) == 2:
            pos = x_pos - width / 2 if i == 0 else x_pos + width / 2
        else:
            offsets = np.linspace(-width, width, len(algo_order))
            pos = x_pos + offsets[i]

        color = PALETTE.get(algo, "gray")
        hatch = HATCHES.get(algo, "")

        ax.bar(
            pos[mask.to_numpy()],
            means[mask].to_numpy(),
            width,
            color=color,
            edgecolor="black",
            linewidth=0.85,
            hatch=hatch,
            alpha=0.98,
            yerr=yerr[:, mask.to_numpy()],
            capsize=4,
            error_kw={"ecolor": "black", "alpha": 0.9, "linewidth": 1.0},
        )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(_format_xticks(x_values))

    ax.set_xlabel(xlabel, labelpad=6)
    ax.set_ylabel(ylabel, labelpad=6)

    _apply_bar_legend_auto(ax, algo_order, prefer="upper")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    if y_axis_mode == "percent_0_1":
        _apply_percent_axis(ax)
    elif y_axis_mode == "thousands_sci":
        _apply_thousands_sci(ax)

    _finalize_layout(fig)
    _save_figure(fig, base_filename_no_ext)
    plt.close(fig)

def plot_line_with_ci(
    df: pd.DataFrame,
    x: str,
    y: str,
    ci_low: str,
    ci_high: str,
    algo_col: str,
    xlabel: str,
    ylabel: str,
    base_filename_no_ext: str,
    *,
    y_axis_mode: str = "plain",
):
    fig, ax = _new_fig_ax()
    algo_order = _algo_order(df, algo_col)

    for algo in algo_order:
        subset = df[df[algo_col] == algo].copy()
        subset[x] = pd.to_numeric(subset[x], errors="coerce")
        subset = subset.dropna(subset=[x]).sort_values(x)

        xs = subset[x].to_numpy()
        ys = pd.to_numeric(subset[y], errors="coerce").to_numpy()
        lo = pd.to_numeric(subset[ci_low], errors="coerce").to_numpy()
        hi = pd.to_numeric(subset[ci_high], errors="coerce").to_numpy()

        color = PALETTE.get(algo, "gray")
        ls = LINESTYLES.get(algo, "-")
        mk = MARKERS.get(algo, "o")

        ax.plot(
            xs,
            ys,
            linewidth=2.0,
            linestyle=ls,
            color=color,
            marker=mk,
            markersize=4.8,
            markeredgewidth=0.85,
            markeredgecolor="black",
        )
        ax.fill_between(xs, lo, hi, alpha=0.14, color=color)

    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    ax.set_xticks(x_values)
    ax.set_xticklabels(_format_xticks(x_values))

    ax.set_xlabel(xlabel, labelpad=6)
    ax.set_ylabel(ylabel, labelpad=6)

    _apply_line_legend_compact(ax, algo_order, prefer="lower")
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    if y_axis_mode == "percent_0_1":
        _apply_percent_axis(ax)

    _finalize_layout(fig)
    _save_figure(fig, base_filename_no_ext)
    plt.close(fig)

# ==========================
# LOAD + PLOT
# ==========================
df = pd.read_csv(INPUT_CSV)
df["scenario_prefix"] = df[COLS["scenario"]].apply(_scenario_prefix)

data_prefixes = list(dict.fromkeys(df["scenario_prefix"].dropna().astype(str).tolist()))
jobs_prefixes = _read_jobs_prefixes(JOBS_JSON)

present_from_jobs = [p for p in jobs_prefixes if p in data_prefixes]
extra_from_data = [p for p in data_prefixes if p not in present_from_jobs]
prefixes = present_from_jobs + extra_from_data

if not prefixes:
    raise SystemExit(f"[ERROR] No scenario prefixes found in {INPUT_CSV}")

for c in ["nodes", "density"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

for prefix in prefixes:
    df_p = df[df["scenario_prefix"] == prefix].copy()
    if df_p.empty:
        print(f"[WARN] No data for prefix={prefix!r} in {INPUT_CSV}")
        continue

    xcol = _xcol_for(prefix, df_p)
    df_p = _numeric(df_p, xcol)
    xlabel = _xlabel_for(prefix)

    # TOTAL PACKETS: generate for all prefixes (you can exclude from paper later)
    if _has_cols(df_p, [COLS["pkt_total_mean"], COLS["pkt_total_ci_low"], COLS["pkt_total_ci_high"]]):
        plot_bar_with_ci(
            df=df_p,
            x=xcol,
            y=COLS["pkt_total_mean"],
            ci_low=COLS["pkt_total_ci_low"],
            ci_high=COLS["pkt_total_ci_high"],
            algo_col=COLS["algorithm"],
            xlabel=xlabel,
            ylabel=Y_LABELS["pkt_total"],
            base_filename_no_ext=f"{prefix}__total_packets",
            y_axis_mode="thousands_sci",
        )
    else:
        print(f"[WARN] Missing total-packets columns for prefix={prefix!r}; skipping packets plot.")

    # CAPACITY (line)
    if _has_cols(df_p, [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_line_with_ci(
            df=df_p,
            x=xcol,
            y=COLS["cap_node_mean"],
            ci_low=COLS["cap_node_ci_low"],
            ci_high=COLS["cap_node_ci_high"],
            algo_col=COLS["algorithm"],
            xlabel=xlabel,
            ylabel=Y_LABELS["cap"],
            base_filename_no_ext=f"{prefix}__capacity_line",
            y_axis_mode="percent_0_1",
        )
    else:
        print(f"[WARN] Missing cap_node_* columns for prefix={prefix!r}; skipping capacity plot.")

print("\nAll plots generated.")
