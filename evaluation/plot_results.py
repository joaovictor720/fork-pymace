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
    x_values = sorted(df[x].dropna().astype(int).unique())
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
    ax.set_xticklabels(x_values)
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

df_B = df[df["scenario"].isin([
    "important_batman",
    "important_ip"
])].copy()

df_B["scenario"] = "scenario_B_mobility_guts"

# ==========================
# PLOTS EXISTENTES
# ==========================
plot_bar_with_ci(
    df_B,
    COLS["nodes"],
    COLS["packets_total_mean"],
    COLS["packets_total_ci_low"],
    COLS["packets_total_ci_high"],
    COLS["algorithm"],
    "Total Packets vs. Swarm Size (Mobile)",
    LABELS["nodes"],
    LABELS["total_packets"],
    "bar_B_total_packets.png"
)

plot_line_with_ci(
    df_B,
    COLS["nodes"],
    COLS["t99_mean"],
    COLS["t99_ci_low"],
    COLS["t99_ci_high"],
    COLS["algorithm"],
    "Convergence Latency ($T_{99}$) vs Swarm Size (Mobility)",
    LABELS["nodes"],
    LABELS["t99"],
    "line_B_convergence99.png"
)

plt.figure(figsize=(8,5))
for algo in df_B[COLS["algorithm"]].dropna().unique():
    dfa = df_B[df_B[COLS["algorithm"]] == algo].sort_values(COLS["nodes"])
    failure = 1.0 - pd.to_numeric(dfa[COLS["success_rate"]], errors="coerce")
    color = PALETTE.get(algo, "gray")
    label = LABELS.get(algo, algo)
    plt.plot(dfa[COLS["nodes"]], failure, marker='s', label=label, color=color, linewidth=2)

x_vals = sorted(df_B[COLS["nodes"]].dropna().astype(int).unique())
if len(x_vals) > 0:
    plt.xticks(x_vals)

plt.title("Convergence Failure Rate vs. Swarm Size", pad=15, fontsize=14, fontweight='bold')
plt.xlabel(LABELS["nodes"], labelpad=10)
plt.ylabel(LABELS["failure_rate"], labelpad=10)
plt.grid(True, linestyle=":", alpha=0.6)
plt.legend(frameon=True)
plt.tight_layout()
plt.savefig(OUTPUT_DIR / "line_B_failure.png", dpi=300)
plt.close()
print("Saved Failure Plot: line_B_failure.png")

# ==========================
# NOVO: CAPACIDADE (DUAS FORMAS)
# ==========================
has_cap_node = all(c in df_B.columns for c in [COLS["cap_node_mean"], COLS["cap_node_ci_low"], COLS["cap_node_ci_high"]])
has_cap_run = all(c in df_B.columns for c in [COLS["cap_run_mean"], COLS["cap_run_ci_low"], COLS["cap_run_ci_high"]])

if has_cap_node:
    plot_line_with_ci(
        df_B,
        COLS["nodes"],
        COLS["cap_node_mean"],
        COLS["cap_node_ci_low"],
        COLS["cap_node_ci_high"],
        COLS["algorithm"],
        "Dissemination Capacity (Pooled by Node) vs Swarm Size",
        LABELS["nodes"],
        LABELS["cap_node"],
        "line_B_capacity_node.png"
    )
    plot_bar_with_ci(
        df_B,
        COLS["nodes"],
        COLS["cap_node_mean"],
        COLS["cap_node_ci_low"],
        COLS["cap_node_ci_high"],
        COLS["algorithm"],
        "Dissemination Capacity (Pooled by Node) vs Swarm Size",
        LABELS["nodes"],
        LABELS["cap_node"],
        "bar_B_capacity_node.png"
    )
else:
    print("[WARN] cap_node_* columns not found; skipping node-pooled capacity plots.")

if has_cap_run:
    plot_line_with_ci(
        df_B,
        COLS["nodes"],
        COLS["cap_run_mean"],
        COLS["cap_run_ci_low"],
        COLS["cap_run_ci_high"],
        COLS["algorithm"],
        "Dissemination Capacity (Mean per Run) vs Swarm Size",
        LABELS["nodes"],
        LABELS["cap_run"],
        "line_B_capacity_run.png"
    )
    plot_bar_with_ci(
        df_B,
        COLS["nodes"],
        COLS["cap_run_mean"],
        COLS["cap_run_ci_low"],
        COLS["cap_run_ci_high"],
        COLS["algorithm"],
        "Dissemination Capacity (Mean per Run) vs Swarm Size",
        LABELS["nodes"],
        LABELS["cap_run"],
        "bar_B_capacity_run.png"
    )
else:
    print("[WARN] cap_run_* columns not found; skipping run-mean capacity plots.")

print("\nAll plots generated successfully!")
