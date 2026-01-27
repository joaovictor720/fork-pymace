import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import numpy as np

# ==========================
# CONFIGURAÇÕES VISUAIS
# ==========================
sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)
PALETTE = {
    "broadcast": "#1f77b4",
    "rapid": "#ff7f0e",
    "multiunicast": "#2ca02c",
}

# ==========================
# CONFIGURAÇÕES GERAIS
# ==========================
INPUT_CSV = "results/aggregated_results.csv"
OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LABELS = {
    "nodes": "Number of Nodes",
    "ops_per_sec": "Operations per Second (Global Load)",

    "avg_packets_per_node": "Average Packets per Node",
    "total_packets": "Total Packets (TX+RX)",

    "broadcast": "Flooding (BATMAN)",
    "rapid": "Gossip (RAPID)",
    "multiunicast": "App Multi-unicast (BATMAN)",

    "t99": "Time to 99% (s)",
    "failure_rate": "Failure Rate",

    "cap_node": "Dissemination Capacity (Pooled by Node, Final Coverage)",
    "cap_run": "Dissemination Capacity (Mean per Run, Final Coverage)",
}

COLS = {
    "scenario": "scenario",
    "algorithm": "algorithm",
    "nodes": "nodes",
    "ops": "ops_per_sec",

    "packets_total_mean": "pkt_total_mean",
    "packets_total_ci_low": "pkt_total_ci_low",
    "packets_total_ci_high": "pkt_total_ci_high",

    "t99_mean": "t99_mean",
    "t99_ci_low": "t99_ci_low",
    "t99_ci_high": "t99_ci_high",

    "success_rate": "success_rate",

    "cap_node_mean": "cap_node_mean",
    "cap_node_ci_low": "cap_node_ci_low",
    "cap_node_ci_high": "cap_node_ci_high",

    "cap_run_mean": "cap_run_mean",
    "cap_run_ci_low": "cap_run_ci_low",
    "cap_run_ci_high": "cap_run_ci_high",
}

def plot_line_with_ci(df, x, y, ci_low, ci_high, algo_col,
                      title, xlabel, ylabel, filename):
    plt.figure(figsize=(8, 5))

    for algo in df[algo_col].dropna().unique():
        dfa = df[df[algo_col] == algo].sort_values(x)
        if dfa.empty:
            continue

        color = PALETTE.get(algo, "gray")
        label = LABELS.get(algo, algo)

        plt.plot(dfa[x], dfa[y], marker='o', label=label, color=color, linewidth=2)
        plt.fill_between(dfa[x], dfa[ci_low], dfa[ci_high], color=color, alpha=0.2)

    x_vals = sorted(df[x].dropna().unique())
    if len(x_vals) > 0:
        plt.xticks(x_vals)

    plt.title(title, pad=15, fontsize=14, fontweight='bold')
    plt.xlabel(xlabel, labelpad=10)
    plt.ylabel(ylabel, labelpad=10)
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend(frameon=True, framealpha=0.9)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / filename, dpi=300)
    plt.close()

    print(f"Saved Line Plot: {filename}")

def plot_bar_with_ci(df, x, y, ci_low, ci_high, algo_col,
                     title, xlabel, ylabel, filename):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    algorithms = df[algo_col].dropna().unique()
    x_values = sorted(pd.to_numeric(df[x], errors="coerce").dropna().unique())
    x_pos = np.arange(len(x_values))
    width = 0.35 if len(algorithms) <= 2 else 0.25

    for i, algo in enumerate(algorithms):
        subset = df[df[algo_col] == algo].sort_values(x)
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
        label = LABELS.get(algo, algo)

        ax.bar(
            pos, means, width, label=label, color=color, alpha=0.9,
            yerr=yerr, capsize=5,
            error_kw={'ecolor': 'black', 'alpha': 0.7}
        )

    ax.set_xticks(x_pos)
    ax.set_xticklabels([str(v).rstrip('0').rstrip('.') if isinstance(v, float) else str(v) for v in x_values])
    ax.set_title(title, pad=15, fontsize=14, fontweight='bold')
    ax.set_xlabel(xlabel, labelpad=10)
    ax.set_ylabel(ylabel, labelpad=10)
    ax.legend(frameon=True, framealpha=0.9)
    ax.grid(True, axis='y', linestyle=":", alpha=0.6)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / filename, dpi=300)
    plt.close()

    print(f"Saved Bar Plot: {filename}")

# ==========================
# CARREGAR DADOS
# ==========================
df = pd.read_csv(INPUT_CSV)

# ==========================================================
# A) workload_diss_sweep_*  (x = diss_per_sec)
# ==========================================================
df_A = df[df["scenario"].isin([
    "workload_diss_sweep_batman",
    "workload_diss_sweep_ip",
])].copy()

if df_A.empty:
    print("[WARN] No rows for workload_diss_sweep_*")
else:
    # Total packets (bar)
    plot_bar_with_ci(
        df_A,
        "diss_per_sec",
        COLS["packets_total_mean"],
        COLS["packets_total_ci_low"],
        COLS["packets_total_ci_high"],
        COLS["algorithm"],
        "Total Packets vs Dissemination Frequency",
        "Disseminations per Second",
        LABELS["total_packets"],
        "bar_A_total_packets_vs_diss.png"
    )

    # Capacity (bar) - pooled-by-node
    if all(c in df_A.columns for c in [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_bar_with_ci(
            df_A,
            "diss_per_sec",
            COLS["cap_node_mean"],
            COLS["cap_node_ci_low"],
            COLS["cap_node_ci_high"],
            COLS["algorithm"],
            "Dissemination Capacity vs Dissemination Frequency (Pooled by Node)",
            "Disseminations per Second",
            LABELS["cap_node"],
            "bar_A_capacity_node_vs_diss.png"
        )
    else:
        print("[WARN] cap_node_* missing for workload_diss_sweep_*; skipping capacity_node.")

    # Capacity (bar) - mean-per-run
    if all(c in df_A.columns for c in [COLS["cap_run_mean"], COLS["cap_run_ci_low"], COLS["cap_run_ci_high"]]):
        plot_bar_with_ci(
            df_A,
            "diss_per_sec",
            COLS["cap_run_mean"],
            COLS["cap_run_ci_low"],
            COLS["cap_run_ci_high"],
            COLS["algorithm"],
            "Dissemination Capacity vs Dissemination Frequency (Mean per Run)",
            "Disseminations per Second",
            LABELS["cap_run"],
            "bar_A_capacity_run_vs_diss.png"
        )
    else:
        print("[WARN] cap_run_* missing for workload_diss_sweep_*; skipping capacity_run.")

# ==========================================================
# B) large_scale_density_*  (x = nodes)
# ==========================================================
df_B = df[df["scenario"].isin([
    "large_scale_density_batman",
    "large_scale_density_ip",
])].copy()

if df_B.empty:
    print("[WARN] No rows for large_scale_density_*")
else:
    plot_bar_with_ci(
        df_B,
        COLS["nodes"],
        COLS["packets_total_mean"],
        COLS["packets_total_ci_low"],
        COLS["packets_total_ci_high"],
        COLS["algorithm"],
        "Total Packets vs Swarm Size (Large Scale)",
        LABELS["nodes"],
        LABELS["total_packets"],
        "bar_B_total_packets_vs_nodes.png"
    )

    if all(c in df_B.columns for c in [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
        plot_bar_with_ci(
            df_B,
            COLS["nodes"],
            COLS["cap_node_mean"],
            COLS["cap_node_ci_low"],
            COLS["cap_node_ci_high"],
            COLS["algorithm"],
            "Dissemination Capacity vs Swarm Size (Pooled by Node)",
            LABELS["nodes"],
            LABELS["cap_node"],
            "bar_B_capacity_node_vs_nodes.png"
        )
    else:
        print("[WARN] cap_node_* missing for large_scale_density_*; skipping capacity_node.")

    if all(c in df_B.columns for c in [COLS["cap_run_mean"], COLS["cap_run_ci_low"], COLS["cap_run_ci_high"]]):
        plot_bar_with_ci(
            df_B,
            COLS["nodes"],
            COLS["cap_run_mean"],
            COLS["cap_run_ci_low"],
            COLS["cap_run_ci_high"],
            COLS["algorithm"],
            "Dissemination Capacity vs Swarm Size (Mean per Run)",
            LABELS["nodes"],
            LABELS["cap_run"],
            "bar_B_capacity_run_vs_nodes.png"
        )
    else:
        print("[WARN] cap_run_* missing for large_scale_density_*; skipping capacity_run.")

# ==========================================================
# C) workload_duration_cooldown_*  (x = duration, split by cooldown)
# ==========================================================
df_C = df[df["scenario"].isin([
    "workload_duration_cooldown_batman",
    "workload_duration_cooldown_ip",
])].copy()

if df_C.empty:
    print("[WARN] No rows for workload_duration_cooldown_*")
else:
    cooldown_values = sorted(pd.to_numeric(df_C.get("cooldown"), errors="coerce").dropna().unique())

    if not cooldown_values:
        print("[WARN] No cooldown values found in workload_duration_cooldown_*; skipping.")
    else:
        for cd in cooldown_values:
            df_cd = df_C[pd.to_numeric(df_C["cooldown"], errors="coerce") == cd].copy()
            if df_cd.empty:
                continue

            suffix = f"cooldown_{str(cd).rstrip('0').rstrip('.') if isinstance(cd, float) else str(cd)}"

            plot_bar_with_ci(
                df_cd,
                "duration",
                COLS["packets_total_mean"],
                COLS["packets_total_ci_low"],
                COLS["packets_total_ci_high"],
                COLS["algorithm"],
                f"Total Packets vs Workload Duration (Cooldown={cd}s)",
                "Workload Duration (s)",
                LABELS["total_packets"],
                f"bar_C_total_packets_vs_duration__{suffix}.png"
            )

            if all(c in df_cd.columns for c in [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]]):
                plot_bar_with_ci(
                    df_cd,
                    "duration",
                    COLS["cap_node_mean"],
                    COLS["cap_node_ci_low"],
                    COLS["cap_node_ci_high"],
                    COLS["algorithm"],
                    f"Dissemination Capacity vs Workload Duration (Pooled by Node, Cooldown={cd}s)",
                    "Workload Duration (s)",
                    LABELS["cap_node"],
                    f"bar_C_capacity_node_vs_duration__{suffix}.png"
                )
            else:
                print(f"[WARN] cap_node_* missing for cooldown={cd}; skipping capacity_node.")

            if all(c in df_cd.columns for c in [COLS["cap_run_mean"], COLS["cap_run_ci_low"], COLS["cap_run_ci_high"]]):
                plot_bar_with_ci(
                    df_cd,
                    "duration",
                    COLS["cap_run_mean"],
                    COLS["cap_run_ci_low"],
                    COLS["cap_run_ci_high"],
                    COLS["algorithm"],
                    f"Dissemination Capacity vs Workload Duration (Mean per Run, Cooldown={cd}s)",
                    "Workload Duration (s)",
                    LABELS["cap_run"],
                    f"bar_C_capacity_run_vs_duration__{suffix}.png"
                )
            else:
                print(f"[WARN] cap_run_* missing for cooldown={cd}; skipping capacity_run.")

print("\nAll plots generated successfully!")