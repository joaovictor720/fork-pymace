import pandas as pd
import numpy as np
from scipy import stats
import sys
import re

INPUT = sys.argv[1]
OUTPUT = sys.argv[2]

df = pd.read_csv(INPUT)

# -----------------------------
# 1. Extrair nodes e ops_per_sec da coluna "variant"
# -----------------------------
def extract_param(variant, key):
    if pd.isna(variant):
        return None
    m = re.search(rf"{key}=([0-9\.]+)", variant)
    if m:
        return float(m.group(1))
    return None

# Garante colunas explícitas
if "variant" in df.columns:
    df["nodes"] = df["variant"].apply(lambda v: extract_param(v, "count"))
    df["ops_per_sec"] = df["variant"].apply(lambda v: extract_param(v, "ops_per_sec"))

# -----------------------------
# 2. Função média + IC 95%
# -----------------------------
def mean_ci(series, confidence=0.95):
    series = series.dropna()
    n = len(series)
    if n == 0:
        return pd.Series({"mean": np.nan, "ci_low": np.nan, "ci_high": np.nan, "std": np.nan, "n": 0})

    mean = series.mean()
    std = series.std(ddof=1)

    if n == 1:
        return pd.Series({"mean": mean, "ci_low": mean, "ci_high": mean, "std": 0.0, "n": 1})

    tval = stats.t.ppf((1 + confidence) / 2.0, n - 1)
    margin = tval * std / np.sqrt(n)

    return pd.Series({
        "mean": mean,
        "ci_low": mean - margin,
        "ci_high": mean + margin,
        "std": std,
        "n": n
    })

# -----------------------------
# 3. Decide agrupamento por cenário
# -----------------------------
def grouping_cols(scenario_name):
    if "scenario_C" in scenario_name or "stress" in scenario_name.lower():
        return ["scenario", "algorithm", "ops_per_sec"]
    else:
        return ["scenario", "algorithm", "nodes"]

rows = []

for scenario_name, df_s in df.groupby("scenario"):
    GROUP_COLS = grouping_cols(scenario_name)

    # Remove linhas onde a variável independente não foi extraída corretamente
    df_s = df_s.dropna(subset=GROUP_COLS)

    for keys, g in df_s.groupby(GROUP_COLS):
        if not isinstance(keys, tuple):
            keys = (keys,)

        row = dict(zip(GROUP_COLS, keys))
        row["runs_total"] = len(g)

        # -----------------------------
        # Confiabilidade (todas as runs)
        # -----------------------------
        row["success_rate"] = g["converged"].mean()
        row["avg_final_coverage"] = g["avg_final_coverage"].mean()
        row["max_abs_error"] = g["max_abs_error"].max()

        # -----------------------------
        # Convergência a 100% (somente runs OK)
        # -----------------------------
        g_ok = g[g["converged"] == True]

        conv = mean_ci(g_ok["convergence_time_s"])
        row["conv_mean"] = conv["mean"]
        row["conv_ci_low"] = conv["ci_low"]
        row["conv_ci_high"] = conv["ci_high"]
        row["conv_std"] = conv["std"]
        row["conv_n"] = conv["n"]

        # -----------------------------
        # Convergência parcial (TODAS as runs)
        # -----------------------------
        t90 = mean_ci(g["time_to_90pct_s"])
        row["t90_mean"] = t90["mean"]
        row["t90_ci_low"] = t90["ci_low"]
        row["t90_ci_high"] = t90["ci_high"]

        t95 = mean_ci(g["time_to_95pct_s"])
        row["t95_mean"] = t95["mean"]
        row["t95_ci_low"] = t95["ci_low"]
        row["t95_ci_high"] = t95["ci_high"]

        t99 = mean_ci(g["time_to_99pct_s"])
        row["t99_mean"] = t99["mean"]
        row["t99_ci_low"] = t99["ci_low"]
        row["t99_ci_high"] = t99["ci_high"]

        # -----------------------------
        # Overhead (todas as runs)
        # -----------------------------
        pkt = mean_ci(g["avg_packets_per_node"])
        row["pkt_node_mean"] = pkt["mean"]
        row["pkt_node_ci_low"] = pkt["ci_low"]
        row["pkt_node_ci_high"] = pkt["ci_high"]

        tot = mean_ci(g["total_packets"])
        row["pkt_total_mean"] = tot["mean"]
        row["pkt_total_ci_low"] = tot["ci_low"]
        row["pkt_total_ci_high"] = tot["ci_high"]

        rx_tx = mean_ci(g["rx_to_tx_ratio"])
        row["rx_tx_mean"] = rx_tx["mean"]
        row["rx_tx_ci_low"] = rx_tx["ci_low"]
        row["rx_tx_ci_high"] = rx_tx["ci_high"]

        rows.append(row)

out = pd.DataFrame(rows)
out.to_csv(OUTPUT, index=False)

print(f"Aggregated results written to: {OUTPUT}")
