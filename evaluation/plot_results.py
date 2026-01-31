import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.ticker import ScalarFormatter
import seaborn as sns

# ==========================
# CONFIG
# ==========================
INPUT_CSV = "results/aggregated_results.csv"
JOBS_JSON = "evaluation/jobs.json"

OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FORMATS = ("pdf",)

# LaTeX-like typography without requiring LaTeX installation
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Computer Modern Roman", "CMU Serif", "DejaVu Serif"],
    "mathtext.fontset": "cm",
    "axes.unicode_minus": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})

sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)

PALETTE = {
    "broadcast": "#1f77b4",
    "rapid": "#ff7f0e",
    "multiunicast": "#2ca02c",
}

LABELS_ALGO = {
    "broadcast": "BATMAN-based Flooding",
    "multiunicast": "Best-effort Multicast",
    "rapid": "Gossip-based Dissemination",
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

def _save_figure(base_filename_no_ext: str):
    for ext in OUTPUT_FORMATS:
        out_path = OUTPUT_DIR / f"{base_filename_no_ext}.{ext}"
        plt.savefig(out_path, bbox_inches="tight")
        print(f"[OK] Saved: {out_path}")

def _apply_percent_axis(ax):
    ax.set_ylim(0.0, 1.0)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0, decimals=0))

def _apply_thousands_sci(ax):
    # Forces ×10^3 style (scientific offset), avoids "50k" formatting
    fmt = ScalarFormatter(useMathText=True)
    fmt.set_scientific(True)
    fmt.set_powerlimits((3, 3))
    ax.yaxis.set_major_formatter(fmt)
    ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3), useMathText=True)

    # Move the scientific offset text slightly away from the legend zone
    off = ax.yaxis.get_offset_text()
    off.set_x(-0.02)     # slightly closer to axis
    off.set_y(1.01)      # slightly up, but not too much (avoids legend overlap)

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
    # Slightly higher than before to avoid overlapping the y-axis scientific offset (×10^3)
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.08),
        ncol=ncol,
        frameon=True,
        framealpha=0.9,
        borderaxespad=0.0,
    )

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
    y_axis_mode: str = "plain",  # "plain" | "percent_0_1" | "thousands_sci"
):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    desired_order = ("multiunicast", "broadcast", "rapid")
    present = set(df[algo_col].dropna().unique())
    algo_order = [a for a in desired_order if a in present]
    if not algo_order:
        algo_order = list(df[algo_col].dropna().unique())

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

        ax.bar(
            pos[mask.to_numpy()],
            means[mask].to_numpy(),
            width,
            label=label,
            color=color,
            alpha=0.9,
            yerr=yerr[:, mask.to_numpy()],
            capsize=5,
            error_kw={"ecolor": "black", "alpha": 0.7},
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

    # Reserve a bit more top space for the legend
    plt.tight_layout(rect=(0, 0, 1, 0.92))
    _save_figure(base_filename_no_ext)
    plt.close()

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
    y_axis_mode: str = "plain",  # "plain" | "percent_0_1"
):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    desired_order = ("multiunicast", "broadcast", "rapid")
    present = set(df[algo_col].dropna().unique())
    algo_order = [a for a in desired_order if a in present]
    if not algo_order:
        algo_order = list(df[algo_col].dropna().unique())

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

        ax.plot(
            xs,
            ys,
            linewidth=2.3,
            label=label,
            color=color,
            marker="o",
            markersize=4.5,
            markeredgewidth=0.6,
            markeredgecolor="black",
        )
        ax.fill_between(xs, lo, hi, alpha=0.18, color=color)

    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    ax.set_xticks(x_values)
    ax.set_xticklabels(_format_xticks(x_values))

    ax.set_xlabel(xlabel, labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)

    _apply_top_legend(ax, ncol=min(3, len(algo_order)))

    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    if y_axis_mode == "percent_0_1":
        _apply_percent_axis(ax)

    plt.tight_layout(rect=(0, 0, 1, 0.92))
    _save_figure(base_filename_no_ext)
    plt.close()

# ==========================
# LOAD
# ==========================
df = pd.read_csv(INPUT_CSV)
df["scenario_prefix"] = df[COLS["scenario"]].apply(_scenario_prefix)

prefixes = _read_jobs_prefixes(JOBS_JSON)
if not prefixes:
    raise SystemExit(f"[ERROR] No scenarios found in {JOBS_JSON}")

for c in ["nodes", "density"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

# ==========================
# PLOT PER PREFIX
# ==========================
for prefix in prefixes:
    df_p = df[df["scenario_prefix"] == prefix].copy()
    if df_p.empty:
        print(f"[WARN] No data for prefix={prefix!r} in {INPUT_CSV}")
        continue

    xcol = _xcol_for(prefix, df_p)
    df_p = _numeric(df_p, xcol)

    xlabel = _xlabel_for(prefix)

    # 1) Total packets (bars only)
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

    # 2) Dissemination capacity (bars + lines)
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
