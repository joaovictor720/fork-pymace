import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ==========================
# CONFIGURAÇÃO VISUAL
# ==========================
sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)
OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ==========================
# CONSTANTES EXPERIMENTO
# ==========================
AREA_KM2 = 0.25  # 500 x 500 m = 0.25 km²

# ==========================
# CARREGAR DADOS
# ==========================
df = pd.read_csv("results/aggregated_results.csv")

# Filtrar: BATMAN + Cenário B (Mobilidade)
df_teaser = df[
    (df["scenario"] == "scenario_B_mobility") &
    (df["algorithm"] == "broadcast")
].copy()

# --------------------------
# Converter nodes -> densidade
# --------------------------
df_teaser["density"] = df_teaser["nodes"] / AREA_KM2
df_teaser = df_teaser.sort_values("density")

# ==========================
# PLOT
# ==========================
plt.figure(figsize=(6, 4))  # compacto para introdução

plt.plot(
    df_teaser["density"],
    df_teaser["t99_mean"],
    marker='o',
    color='#d62728',
    linewidth=2.5,
    label="BATMAN Latency"
)

plt.fill_between(
    df_teaser["density"],
    df_teaser["t99_ci_low"],
    df_teaser["t99_ci_high"],
    color='#d62728',
    alpha=0.2
)

x_vals = sorted(df_teaser["density"].unique())
plt.xticks(x_vals)

# --------------------------
# Estética
# --------------------------
plt.title(
    "Impact of Network Density on Convergence Latency",
    fontsize=12,
    fontweight='bold'
)
plt.xlabel("Swarm Density (nodes / km²)", fontsize=11)
plt.ylabel("Time to 99% Coverage (s)", fontsize=11)

plt.grid(True, linestyle=":", alpha=0.6)

# --------------------------
# Anotação opcional (explosão)
# --------------------------
if 60 in df_teaser["nodes"].values:
    row = df_teaser[df_teaser["nodes"] == 60].iloc[0]
    plt.annotate(
        "Rapid Growth\n(Broadcast Storm)",
        xy=(row["density"], row["t99_mean"]),
        xytext=(row["density"] * 0.6, row["t99_mean"]),
        arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=8),
        fontsize=9
    )

plt.tight_layout()
plt.savefig(OUTPUT_DIR / "teaser_batman_latency.png", dpi=300)
plt.close()

print("Teaser gerado com sucesso!")
