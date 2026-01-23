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
    "broadcast": "#1f77b4",  # Azul (BATMAN)
    "rapid": "#ff7f0e"       # Laranja (RAPID)
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

    "t99": "Time to 99% (s)",
    "t100": "Time to 100% (s)",
    "failure_rate": "Failure Rate"
}

COLS = {
    "scenario": "scenario",
    "algorithm": "algorithm",
    "nodes": "nodes",
    "ops": "ops_per_sec",

    # --- Per-node packets (already present) ---
    "packets_mean": "pkt_node_mean",
    "packets_ci_low": "pkt_node_ci_low",
    "packets_ci_high": "pkt_node_ci_high",

    # --- Total packets (NEW) ---
    "packets_total_mean": "pkt_total_mean",
    "packets_total_ci_low": "pkt_total_ci_low",
    "packets_total_ci_high": "pkt_total_ci_high",

    "conv_mean": "conv_mean",
    "conv_ci_low": "conv_ci_low",
    "conv_ci_high": "conv_ci_high",

    "t99_mean": "t99_mean",
    "t99_ci_low": "t99_ci_low",
    "t99_ci_high": "t99_ci_high",

    "success_rate": "success_rate"
}

# ==========================
# FUNÇÃO DE PLOT DE LINHAS (COM SOMBRA DE CI)
# ==========================
def plot_line_with_ci(df, x, y, ci_low, ci_high, algo_col,
                      title, xlabel, ylabel, filename):
    plt.figure(figsize=(8, 5))

    for algo in df[algo_col].unique():
        dfa = df[df[algo_col] == algo].sort_values(x)
        color = PALETTE.get(algo, "gray")
        label = LABELS.get(algo, algo)
        plt.plot(dfa[x], dfa[y], marker='o', label=label, color=color, linewidth=2)
        plt.fill_between(dfa[x], dfa[ci_low], dfa[ci_high], color=color, alpha=0.2)

    # 🔧 Força ticks apenas nos valores reais de X
    x_vals = sorted(df[x].dropna().unique())
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

# ==========================
# FUNÇÃO DE PLOT DE BARRAS (COM ERROR BARS)
# ==========================
def plot_bar_with_ci(df, x, y, ci_low, ci_high, algo_col,
                     title, xlabel, ylabel, filename):
    plt.figure(figsize=(8, 5))
    ax = plt.gca()

    algorithms = df[algo_col].unique()

    # 🔧 CONVERSÃO PARA INTEIROS AQUI
    x_values = sorted(df[x].dropna().astype(int).unique())
    x_pos = np.arange(len(x_values))
    width = 0.35

    for i, algo in enumerate(algorithms):
        subset = df[df[algo_col] == algo].sort_values(x)

        # Garante alinhamento entre todos os X
        metrics = pd.DataFrame({x: x_values}).merge(subset, on=x, how='left')

        means = metrics[y].fillna(0)
        yerr = [
            means - metrics[ci_low].fillna(0),
            metrics[ci_high].fillna(0) - means
        ]

        pos = x_pos - width/2 if i == 0 else x_pos + width/2
        color = PALETTE.get(algo, "gray")
        label = LABELS.get(algo, algo)

        ax.bar(
            pos, means, width, label=label, color=color, alpha=0.9,
            yerr=yerr, capsize=5,
            error_kw={'ecolor': 'black', 'alpha': 0.7}
        )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(x_values)  # Agora sem .0
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

# df_A = df[df[COLS["scenario"]] == "scenario_A_baseline"]
df_B = df[df["scenario"].isin([
    "important_batman",
    "important_ip",
])].copy()

# (Opcional, mas recomendado) Normaliza o nome pra aparecer como um único cenário
df_B["scenario"] = "important"
# df_C = df[df[COLS["scenario"]] == "scenario_C_stress"]

# ==========================
# GERAR TODOS OS GRÁFICOS
# ==========================

# 1) Bar Charts de Overhead (TOTAL PACKETS)
plot_bar_with_ci(
    df_B,
    COLS["nodes"],
    COLS["packets_total_mean"],
    COLS["packets_total_ci_low"],
    COLS["packets_total_ci_high"],
    COLS["algorithm"],
    "Scenario B — Total Packets vs. Swarm Size (Mobile)",
    LABELS["nodes"],
    LABELS["total_packets"],
    "bar_B_total_packets.png"
)

# 2) Line Charts de Convergência
plot_line_with_ci(
    df_B,
    COLS["nodes"],
    COLS["t99_mean"],
    COLS["t99_ci_low"],
    COLS["t99_ci_high"],
    COLS["algorithm"],
    "Scenario B — Convergence Latency ($T_{99}$) vs Swarm Size (Mobility)",
    LABELS["nodes"],
    LABELS["t99"],
    "line_B_convergence99.png"
)

# 3) Failure Rate (Mobilidade)
plt.figure(figsize=(8,5))
for algo in df_B[COLS["algorithm"]].unique():
    dfa = df_B[df_B[COLS["algorithm"]] == algo].sort_values(COLS["nodes"])
    failure = 1.0 - dfa[COLS["success_rate"]]
    color = PALETTE.get(algo, "gray")
    label = LABELS.get(algo, algo)
    plt.plot(dfa[COLS["nodes"]], failure, marker='s', label=label, color=color, linewidth=2)

# 🔧 Força ticks apenas nos valores reais de densidade
x_vals = sorted(df_B[COLS["nodes"]].dropna().astype(int).unique())
plt.xticks(x_vals)

plt.title("Scenario B — Convergence Failure Rate vs. Swarm Size", pad=15, fontsize=14, fontweight='bold')
plt.xlabel(LABELS["nodes"], labelpad=10)
plt.ylabel(LABELS["failure_rate"], labelpad=10)
plt.grid(True, linestyle=":", alpha=0.6)
plt.legend(frameon=True)
plt.tight_layout()
plt.savefig(OUTPUT_DIR / "line_B_failure.png", dpi=300)
plt.close()

print("Saved Failure Plot: line_B_failure.png")
print("\nAll plots generated successfully!")
