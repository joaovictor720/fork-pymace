import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from pathlib import Path

# ==========================
# CONFIGURAÇÕES GERAIS
# ==========================
OUTPUT_DIR = Path("results/plots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

N_NODES = 10
AREA_SIZE = 100.0
COMM_RANGE = 30.0
SEED = 42

np.random.seed(SEED)

# ==========================
# GERAR POSIÇÕES (CONCENTRADAS NO CENTRO)
# ==========================
positions = np.random.normal(
    loc=AREA_SIZE / 2,
    scale=AREA_SIZE / 5,
    size=(N_NODES, 2)
)
positions = np.clip(positions, 0, AREA_SIZE)

# ==========================
# FUNÇÃO DE DISTÂNCIA
# ==========================
def dist(a, b):
    return np.linalg.norm(a - b)

# ==========================
# PLOT
# ==========================
fig, ax = plt.subplots(figsize=(6, 6))

# --- Desenhar raios de comunicação ---
for i in range(N_NODES):
    circle = Circle(
        positions[i],
        COMM_RANGE,
        color="#1f77b4",
        alpha=0.08,
        linewidth=0,
        zorder=0
    )
    ax.add_patch(circle)

# --- Desenhar links válidos (1-hop) ---
for i in range(N_NODES):
    for j in range(i + 1, N_NODES):
        if dist(positions[i], positions[j]) <= COMM_RANGE:
            ax.plot(
                [positions[i, 0], positions[j, 0]],
                [positions[i, 1], positions[j, 1]],
                color="gray",
                alpha=0.6,
                linewidth=0.8,
                zorder=1
            )

# --- Desenhar nós ---
ax.scatter(
    positions[:, 0],
    positions[:, 1],
    s=80,
    color="#1f77b4",
    edgecolors="black",
    linewidths=0.6,
    zorder=2
)

# ==========================
# ESTÉTICA FINAL
# ==========================
ax.set_title(
    "Multi-hop Connectivity in a CPS Swarm\n(Limited Communication Range)",
    fontsize=12,
    fontweight="bold",
    pad=10
)

ax.set_xlabel("X Position (m)")
ax.set_ylabel("Y Position (m)")

ax.set_xlim(0, AREA_SIZE)
ax.set_ylim(0, AREA_SIZE)
ax.set_aspect("equal", adjustable="box")

ax.grid(True, linestyle=":", alpha=0.4)

plt.tight_layout()
plt.savefig(
    OUTPUT_DIR / "swarm_multihop.png",
    dpi=300
)
plt.close()

print("Figura salva em: results/plots/swarm_multihop.png")
