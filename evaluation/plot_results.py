import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# ==========================
# CONFIG
# ==========================
INPUT_CSV = "results/aggregated_results.csv"
JOBS_JSON = "evaluation/jobs.json"
OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)

PALETTE = {
    "broadcast": "#1f77b4",
    "rapid": "#ff7f0e",
    "multiunicast": "#2ca02c",
}

LABELS_ALGO = {
    "broadcast": "Flooding (BATMAN)",
    "multiunicast": "App Multi-unicast (BATMAN)",
    "rapid": "Gossip (RAPID)",
}

Y_LABELS = {
    "cap": "Dissemination Capacity",
    "pkt_total": "Total Packets (TX+RX)",
}

COLS = {
    "scenario": "scenario",
    "algorithm": "algorithm",
    "nodes": "nodes",
    "ops": "ops_per_sec",

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

def _numeric(df, col):
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def _has_cols(df, cols):
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
                s = f"{fv:.6g}"
                out.append(s)
        except Exception:
            out.append(str(v))
    return out

def plot_bar_with_ci(df, x, y, ci_low, ci_high, algo_col,
                     title, xlabel, ylabel, filename):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    algorithms = list(df[algo_col].dropna().unique())
    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    x_pos = np.arange(len(x_values))
    width = 0.35 if len(algorithms) <= 2 else 0.25

    for i, algo in enumerate(algorithms):
        subset = df[df[algo_col] == algo].copy()
        subset = subset.sort_values(x)
        metrics = pd.DataFrame({x: x_values}).merge(subset, on=x, how='left')

        means = pd.to_numeric(metrics[y], errors="coerce").fillna(0.0)
        lo = pd.to_numeric(metrics[ci_low], errors="coerce").fillna(means)
        hi = pd.to_numeric(metrics[ci_high], errors="coerce").fillna(means)
        yerr = [means - lo, hi - means]

        if len(algorithms) == 1:
            pos = x_pos
        elif len(algorithms) == 2:
            pos = x_pos - width/2 if i == 0 else x_pos + width/2
        else:
            offsets = np.linspace(-width, width, len(algorithms))
            pos = x_pos + offsets[i]

        color = PALETTE.get(algo, "gray")
        label = LABELS_ALGO.get(algo, algo)

        ax.bar(
            pos, means, width,
            label=label,
            color=color, alpha=0.9,
            yerr=yerr, capsize=5,
            error_kw={"ecolor": "black", "alpha": 0.7}
        )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(_format_xticks(x_values))
    ax.set_title(title, pad=15, fontsize=14, fontweight='bold')
    ax.set_xlabel(xlabel, labelpad=10)
    ax.set_ylabel(ylabel, labelpad=10)
    ax.legend(frameon=True, framealpha=0.9)
    ax.grid(True, axis='y', linestyle=":", alpha=0.6)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / filename, dpi=300)
    plt.close()

    print(f"[OK] Saved: {filename}")

# ==========================
# LOAD
# ==========================
df = pd.read_csv(INPUT_CSV)

# normalize scenario base prefix: remove _batman/_ip suffix
def _scenario_prefix(s):
    s = str(s)
    if s.endswith("_batman"):
        return s[:-len("_batman")]
    if s.endswith("_ip"):
        return s[:-len("_ip")]
    return s

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

    # 1) Total packets
    if _has_cols(df_p, [COLS["pkt_total_mean"], COLS["pkt_total_ci_low"], COLS["pkt_total_ci_high"]]):
        plot_bar_with_ci(
            df_p,
            xcol,
            COLS["pkt_total_mean"],
            COLS["pkt_total_ci_low"],
            COLS["pkt_total_ci_high"],
            COLS["algorithm"],
            f"Total Packets vs {_pretty_xlabel(xcol)} ({prefix})",
            _pretty_xlabel(xcol),
            Y_LABELS["pkt_total"],
            f"{prefix}__total_packets.png"
        )
    else:
        print(f"[WARN] Missing total-packets columns for prefix={prefix!r}; skipping packets plot.")

    # 2) Dissemination capacity (node-pooled only; label without 'pooled by node')
    if _has_cols(df_p, [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_bar_with_ci(
            df_p,
            xcol,
            COLS["cap_node_mean"],
            COLS["cap_node_ci_low"],
            COLS["cap_node_ci_high"],
            COLS["algorithm"],
            f"Dissemination Capacity vs {_pretty_xlabel(xcol)} ({prefix})",
            _pretty_xlabel(xcol),
            Y_LABELS["cap"],
            f"{prefix}__capacity.png"
        )
    else:
        print(f"[WARN] Missing cap_node_* columns for prefix={prefix!r}; skipping capacity plot.")

print("\nAll plots generated.")
