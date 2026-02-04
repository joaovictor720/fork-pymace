import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.ticker import ScalarFormatter
import seaborn as sns

INPUT_CSV = "results/aggregated_results.csv"
JOBS_JSON = "evaluation/jobs.json"

OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FORMATS = ("pdf",)

BASE_FONT_SCALE = 1.1
FONT_SCALE = 1.6
FIG_SCALE = FONT_SCALE / BASE_FONT_SCALE

BASE_FIGSIZE = (8, 5)

STYLE_RC = {
    "font.family": "serif",
    "font.serif": ["CMU Serif", "Computer Modern Roman", "DejaVu Serif"],
    "mathtext.fontset": "cm",
    "axes.unicode_minus": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "font.size": 11.0 * FONT_SCALE,
    "axes.labelsize": 11.0 * FONT_SCALE,
    "axes.titlesize": 12.0 * FONT_SCALE,
    "xtick.labelsize": 10.0 * FONT_SCALE,
    "ytick.labelsize": 10.0 * FONT_SCALE,
    "legend.fontsize": 10.0 * FONT_SCALE,
}

sns.set_theme(style="whitegrid", context="paper", rc=STYLE_RC)
plt.rcParams.update(STYLE_RC)

PALETTE = {
    "broadcast": "#1f77b4",
    "rapid": "#9467bd",
    "multiunicast": "#2ca02c",
}

LINESTYLES = {
    "multiunicast": "--",
    "broadcast": "-",
    "rapid": "-.",
}

MARKERS = {
    "multiunicast": "s",
    "broadcast": "o",
    "rapid": "^",
}

HATCHES = {
    "multiunicast": "///",
    "broadcast": "\\\\",
    "rapid": "xx",
}

LABELS_ALGO = {
    "broadcast": "Flooding Multicast",
    "multiunicast": "Best-effort Multicast",
    "rapid": "Gossip-based Multicast",
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
        fig.savefig(out_path, bbox_inches="tight")
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

def _apply_top_legend(ax, ncol: int):
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.08),
        ncol=ncol,
        frameon=True,
        framealpha=0.95,
        borderaxespad=0.0,
        handlelength=2.4,
    )

def _new_fig_ax():
    w, h = BASE_FIGSIZE
    fig, ax = plt.subplots(figsize=(w * FIG_SCALE, h * FIG_SCALE))
    return fig, ax

def _finalize_layout(fig):
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.14, top=0.80)

def _algo_order(df: pd.DataFrame, algo_col: str):
    desired_order = ("multiunicast", "broadcast", "rapid")
    present = set(df[algo_col].dropna().unique())
    algo_order = [a for a in desired_order if a in present]
    if not algo_order:
        algo_order = list(df[algo_col].dropna().unique())
    return algo_order

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
        width = 0.55
    elif len(algo_order) == 2:
        width = 0.35
    else:
        width = 0.25

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
        label = LABELS_ALGO.get(algo, algo)
        hatch = HATCHES.get(algo, "")

        ax.bar(
            pos[mask.to_numpy()],
            means[mask].to_numpy(),
            width,
            label=label,
            color=color,
            edgecolor="black",
            linewidth=0.8,
            hatch=hatch,
            alpha=0.95,
            yerr=yerr[:, mask.to_numpy()],
            capsize=5,
            error_kw={"ecolor": "black", "alpha": 0.85, "linewidth": 1.0},
        )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(_format_xticks(x_values))
    ax.set_xlabel(xlabel, labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)

    _apply_top_legend(ax, ncol=min(3, len(algo_order)))
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
        label = LABELS_ALGO.get(algo, algo)
        ls = LINESTYLES.get(algo, "-")
        mk = MARKERS.get(algo, "o")

        ax.plot(
            xs,
            ys,
            linewidth=2.3,
            linestyle=ls,
            label=label,
            color=color,
            marker=mk,
            markersize=5.0,
            markeredgewidth=0.8,
            markeredgecolor="black",
        )
        ax.fill_between(xs, lo, hi, alpha=0.16, color=color)

    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    ax.set_xticks(x_values)
    ax.set_xticklabels(_format_xticks(x_values))

    ax.set_xlabel(xlabel, labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)

    _apply_top_legend(ax, ncol=min(3, len(algo_order)))
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    if y_axis_mode == "percent_0_1":
        _apply_percent_axis(ax)

    _finalize_layout(fig)
    _save_figure(fig, base_filename_no_ext)
    plt.close(fig)

df = pd.read_csv(INPUT_CSV)
df["scenario_prefix"] = df[COLS["scenario"]].apply(_scenario_prefix)

prefixes = _read_jobs_prefixes(JOBS_JSON)
if not prefixes:
    raise SystemExit(f"[ERROR] No scenarios found in {JOBS_JSON}")

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

    if _has_cols(df_p, [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_bar_with_ci(
            df=df_p,
            x=xcol,
            y=COLS["cap_node_mean"],
            ci_low=COLS["cap_node_ci_low"],
            ci_high=COLS["cap_node_ci_high"],
            algo_col=COLS["algorithm"],
            xlabel=xlabel,
            ylabel=Y_LABELS["cap"],
            base_filename_no_ext=f"{prefix}__capacity_bar",
            y_axis_mode="percent_0_1",
        )

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
        print(f"[WARN] Missing cap_node_* columns for prefix={prefix!r}; skipping capacity plots.")

print("\nAll plots generated.")
