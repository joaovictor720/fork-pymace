import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

# ==========================
# CONFIG
# ==========================
INPUT_CSV = "results/aggregated_results.csv"
JOBS_JSON = "evaluation/jobs.json"

OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Choose output formats: any subset of {"pdf", "png"}
OUTPUT_FORMATS = ("pdf",)  # e.g. ("pdf",) or ("pdf", "png")

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

def _pick_x_axis_for_prefix(df_prefix: pd.DataFrame):
    candidates = [
        "nodes",
        "ops_per_sec",
        "diss_per_sec",
        "duration",
        "cooldown",
        "dissemination_interval",
        "density",
    ]
    for c in candidates:
        if c in df_prefix.columns and df_prefix[c].notna().any():
            uniq = pd.to_numeric(df_prefix[c], errors="coerce").dropna().unique()
            if len(uniq) >= 2:
                return c
    for c in candidates:
        if c in df_prefix.columns and df_prefix[c].notna().any():
            return c
    return None

def _pretty_xlabel(xcol: str):
    mapping = {
        "nodes": "Number of Nodes",
        "ops_per_sec": "Operations per Second (Global Load)",
        "diss_per_sec": "Disseminations per Second",
        "duration": "Workload Duration (s)",
        "cooldown": "Cooldown (s)",
        "dissemination_interval": "Dissemination Interval (s)",
        "density": "Density (nodes/km²)",
    }
    return mapping.get(xcol, xcol)

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
        kwargs = {"bbox_inches": "tight"}
        if ext.lower() == "png":
            kwargs["dpi"] = 300
        plt.savefig(out_path, **kwargs)
        print(f"[OK] Saved: {out_path}")

def _apply_kilo_formatter(ax):
    """
    Format Y axis as x1000 (k) for readability. Example: 350000 -> 350k
    Keeps the label unchanged.
    """
    def _fmt(v, _pos):
        av = abs(v)
        if av >= 1_000_000:
            return f"{v/1_000_000:.3g}M"
        if av >= 1_000:
            return f"{v/1_000:.3g}k"
        if av >= 1:
            return f"{v:.3g}"
        return f"{v:.3g}"
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt))

def _apply_percent_axis(ax):
    """
    Format Y axis as percent (0..1 -> 0%..100%).
    Also makes ticks nice and clamps to [0, 1] visually.
    """
    ax.set_ylim(0.0, 1.0)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0, decimals=0))

def plot_bar_with_ci(
    df: pd.DataFrame,
    x: str,
    y: str,
    ci_low: str,
    ci_high: str,
    algo_col: str,
    title: str,
    xlabel: str,
    ylabel: str,
    base_filename_no_ext: str,
    *,
    y_axis_mode: str = "plain",  # "plain" | "percent_0_1" | "kilo"
):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    desired_order = ("multiunicast", "broadcast", "rapid")  # left -> middle -> right
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
    ax.set_title(title, pad=12, fontsize=14, fontweight="bold")
    ax.set_xlabel(xlabel, labelpad=8)
    ax.set_ylabel(ylabel, labelpad=8)
    ax.legend(frameon=True, framealpha=0.9)
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)

    if y_axis_mode == "percent_0_1":
        _apply_percent_axis(ax)
    elif y_axis_mode == "kilo":
        _apply_kilo_formatter(ax)

    plt.tight_layout()
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

# ==========================
# PLOT PER PREFIX
# ==========================
for prefix in prefixes:
    df_p = df[df["scenario_prefix"] == prefix].copy()
    if df_p.empty:
        print(f"[WARN] No data for prefix={prefix!r} in {INPUT_CSV}")
        continue

    xcol = _pick_x_axis_for_prefix(df_p)
    if not xcol:
        print(f"[WARN] Could not pick x-axis for prefix={prefix!r}; skipping.")
        continue

    df_p = _numeric(df_p, xcol)

    # 1) Total packets (keep label as-is, format Y in k/M)
    if _has_cols(df_p, [COLS["pkt_total_mean"], COLS["pkt_total_ci_low"], COLS["pkt_total_ci_high"]]):
        plot_bar_with_ci(
            df=df_p,
            x=xcol,
            y=COLS["pkt_total_mean"],
            ci_low=COLS["pkt_total_ci_low"],
            ci_high=COLS["pkt_total_ci_high"],
            algo_col=COLS["algorithm"],
            title="",
            xlabel=_pretty_xlabel(xcol),
            ylabel=Y_LABELS["pkt_total"],
            base_filename_no_ext=f"{prefix}__total_packets",
            y_axis_mode="kilo",
        )
    else:
        print(f"[WARN] Missing total-packets columns for prefix={prefix!r}; skipping packets plot.")

    # 2) Dissemination capacity (show as percent 0..100%)
    if _has_cols(df_p, [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_bar_with_ci(
            df=df_p,
            x=xcol,
            y=COLS["cap_node_mean"],
            ci_low=COLS["cap_node_ci_low"],
            ci_high=COLS["cap_node_ci_high"],
            algo_col=COLS["algorithm"],
            title="",
            xlabel=_pretty_xlabel(xcol),
            ylabel=Y_LABELS["cap"],
            base_filename_no_ext=f"{prefix}__capacity",
            y_axis_mode="percent_0_1",
        )
    else:
        print(f"[WARN] Missing cap_node_* columns for prefix={prefix!r}; skipping capacity plot.")

print("\nAll plots generated.")
