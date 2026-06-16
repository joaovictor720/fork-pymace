import pandas as pd
import numpy as np
from scipy import stats
import sys
import re
from pathlib import Path

INPUT = sys.argv[1]
OUTPUT = sys.argv[2]

df = pd.read_csv(INPUT)

# -----------------------------
# 0) Preferir nodes_cfg/density vindos do scenario.json (coletados em collect_all_results.py)
# -----------------------------
if "nodes_cfg" in df.columns:
    df["nodes_cfg"] = pd.to_numeric(df["nodes_cfg"], errors="coerce")
    df["nodes"] = df["nodes_cfg"]

if "density" in df.columns:
    df["density"] = pd.to_numeric(df["density"], errors="coerce")

# -----------------------------
# 1) Extrair params da coluna "variant" (mantém compatibilidade com cenários antigos)
# -----------------------------
def extract_param(variant, key):
    if pd.isna(variant):
        return None
    m = re.search(rf"{key}=([0-9\.]+)", str(variant))
    if m:
        return float(m.group(1))
    return None

nodes_orig = None
if "nodes" in df.columns:
    nodes_orig = pd.to_numeric(df["nodes"], errors="coerce")

if "variant" in df.columns:
    # só preenche se ainda não houver nodes vindo de nodes_cfg
    if "nodes_cfg" not in df.columns:
        df["nodes"] = df["variant"].apply(lambda v: extract_param(v, "count"))
        if nodes_orig is not None:
            df["nodes"] = pd.to_numeric(df["nodes"], errors="coerce").fillna(nodes_orig)

    df["ops_per_sec"] = df["variant"].apply(lambda v: extract_param(v, "ops_per_sec"))
    df["diss_per_sec"] = df["variant"].apply(lambda v: extract_param(v, "diss_per_sec"))
    df["duration"] = df["variant"].apply(lambda v: extract_param(v, "duration"))
    df["cooldown"] = df["variant"].apply(lambda v: extract_param(v, "cooldown"))
    df["error"] = df["variant"].apply(lambda v: extract_param(v, "error"))

# -----------------------------
# 2) Função média + IC 95%
# -----------------------------
def mean_ci(series, confidence=0.95):
    series = pd.to_numeric(series, errors="coerce").dropna()
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

def keep_existing_cols(df_, cols):
    return [c for c in cols if c in df_.columns]


def copy_constant_cfg_columns(row, group_df):
    for col in group_df.columns:
        if not (
            col.startswith("cfg_")
            or col.startswith("meta_")
            or col.startswith("appmeta_")
            or col in {"app_name", "algorithm_base", "algorithm_label"}
        ):
            continue
        values = group_df[col].dropna().unique()
        if len(values) == 1:
            row[col] = values[0]

# -----------------------------
# 3) Decide agrupamento por cenário
# -----------------------------
def grouping_cols(scenario_name, scenario_df=None):
    s = str(scenario_name)

    # seus cenários do artigo
    if s.startswith("density_") or s.startswith("area_"):
        # queremos X em densidade (nodes/km^2), independentemente do que varia no JSON
        if scenario_df is not None and "density" in scenario_df.columns:
            density_series = pd.to_numeric(scenario_df["density"], errors="coerce")
            if density_series.notna().any():
                return ["scenario", "algorithm", "density"]
        return ["scenario", "algorithm", "nodes"]

    if s.startswith("scalability_"):
        return ["scenario", "algorithm", "nodes"]

    # cenários antigos/gerais
    if "packet_loss" in s:
        return ["scenario", "algorithm", "error"]

    if "workload_diss_sweep" in s:
        return ["scenario", "algorithm", "diss_per_sec"]

    if "large_scale_density" in s:
        return ["scenario", "algorithm", "nodes"]

    if "workload_duration_cooldown" in s:
        return ["scenario", "algorithm", "duration", "cooldown"]

    if "scenario_C" in s or "stress" in s.lower():
        return ["scenario", "algorithm", "ops_per_sec"]

    return ["scenario", "algorithm", "nodes"]

rows = []

for scenario_name, df_s in df.groupby("scenario"):
    GROUP_COLS = grouping_cols(scenario_name, df_s)

    GROUP_COLS = keep_existing_cols(df_s, GROUP_COLS)
    if not GROUP_COLS:
        continue

    df_s = df_s.dropna(subset=GROUP_COLS)

    for keys, g in df_s.groupby(GROUP_COLS):
        if not isinstance(keys, tuple):
            keys = (keys,)

        row = dict(zip(GROUP_COLS, keys))
        copy_constant_cfg_columns(row, g)
        row["runs_total"] = len(g)

        row["success_rate"] = pd.to_numeric(g.get("converged"), errors="coerce").mean()
        row["avg_final_coverage"] = pd.to_numeric(g.get("avg_final_coverage"), errors="coerce").mean()
        row["max_abs_error"] = pd.to_numeric(g.get("max_abs_error"), errors="coerce").max()

        g_ok = g[g.get("converged") == True] if "converged" in g.columns else g.iloc[0:0]

        conv = mean_ci(g_ok.get("convergence_time_s"))
        row["conv_mean"] = conv["mean"]
        row["conv_ci_low"] = conv["ci_low"]
        row["conv_ci_high"] = conv["ci_high"]
        row["conv_std"] = conv["std"]
        row["conv_n"] = conv["n"]

        t90 = mean_ci(g.get("time_to_90pct_s"))
        row["t90_mean"] = t90["mean"]
        row["t90_ci_low"] = t90["ci_low"]
        row["t90_ci_high"] = t90["ci_high"]

        t95 = mean_ci(g.get("time_to_95pct_s"))
        row["t95_mean"] = t95["mean"]
        row["t95_ci_low"] = t95["ci_low"]
        row["t95_ci_high"] = t95["ci_high"]

        t99 = mean_ci(g.get("time_to_99pct_s"))
        row["t99_mean"] = t99["mean"]
        row["t99_ci_low"] = t99["ci_low"]
        row["t99_ci_high"] = t99["ci_high"]

        pkt = mean_ci(g.get("avg_packets_per_node"))
        row["pkt_node_mean"] = pkt["mean"]
        row["pkt_node_ci_low"] = pkt["ci_low"]
        row["pkt_node_ci_high"] = pkt["ci_high"]

        tot = mean_ci(g.get("total_packets"))
        row["pkt_total_mean"] = tot["mean"]
        row["pkt_total_ci_low"] = tot["ci_low"]
        row["pkt_total_ci_high"] = tot["ci_high"]

        rx_tx = mean_ci(g.get("rx_to_tx_ratio"))
        row["rx_tx_mean"] = rx_tx["mean"]
        row["rx_tx_ci_low"] = rx_tx["ci_low"]
        row["rx_tx_ci_high"] = rx_tx["ci_high"]

        rows.append(row)

out = pd.DataFrame(rows)

def merge_keys_from(cap_df, out_df):
    metric_cols = {
        "cap_node_mean","cap_node_ci_low","cap_node_ci_high","cap_node_std","cap_node_n",
        "cap_run_mean","cap_run_ci_low","cap_run_ci_high","cap_run_std","cap_run_n"
    }
    return [c for c in cap_df.columns if c in out_df.columns and c not in metric_cols]

# -----------------------------
# 4) Capacidade de disseminação (pooled por nó e por run)
# -----------------------------
cap_path = Path(INPUT).with_name("all_capacity_samples.csv")
if cap_path.exists():
    cap = pd.read_csv(cap_path)

    if "nodes_cfg" in cap.columns:
        cap["nodes_cfg"] = pd.to_numeric(cap["nodes_cfg"], errors="coerce")
        cap["nodes"] = cap["nodes_cfg"]

    if "density" in cap.columns:
        cap["density"] = pd.to_numeric(cap["density"], errors="coerce")

    # compat (caso não exista nodes_cfg)
    cap_nodes_orig = None
    if "nodes" in cap.columns:
        cap_nodes_orig = pd.to_numeric(cap["nodes"], errors="coerce")

    if "variant" in cap.columns:
        if "nodes_cfg" not in cap.columns:
            cap["nodes"] = cap["variant"].apply(lambda v: extract_param(v, "count"))
            if cap_nodes_orig is not None:
                cap["nodes"] = pd.to_numeric(cap["nodes"], errors="coerce").fillna(cap_nodes_orig)

        cap["ops_per_sec"] = cap["variant"].apply(lambda v: extract_param(v, "ops_per_sec"))
        cap["diss_per_sec"] = cap["variant"].apply(lambda v: extract_param(v, "diss_per_sec"))
        cap["duration"] = cap["variant"].apply(lambda v: extract_param(v, "duration"))
        cap["cooldown"] = cap["variant"].apply(lambda v: extract_param(v, "cooldown"))
        cap["error"] = cap["variant"].apply(lambda v: extract_param(v, "error"))

    cap["coverage"] = pd.to_numeric(cap.get("coverage"), errors="coerce")

    # pooled por nó
    cap_node_rows = []
    for scenario_name, cap_s in cap.groupby("scenario"):
        GROUP_COLS = grouping_cols(scenario_name, cap_s)
        GROUP_COLS = keep_existing_cols(cap_s, GROUP_COLS)
        if not GROUP_COLS:
            continue
        cap_s = cap_s.dropna(subset=GROUP_COLS)

        for keys, g in cap_s.groupby(GROUP_COLS):
            if not isinstance(keys, tuple):
                keys = (keys,)
            row = dict(zip(GROUP_COLS, keys))

            mc = mean_ci(g["coverage"])
            row["cap_node_mean"] = mc["mean"]
            row["cap_node_ci_low"] = mc["ci_low"]
            row["cap_node_ci_high"] = mc["ci_high"]
            row["cap_node_std"] = mc["std"]
            row["cap_node_n"] = mc["n"]
            cap_node_rows.append(row)

    cap_node_out = pd.DataFrame(cap_node_rows)

    # por run
    cap_run_rows = []
    if "run" in cap.columns:
        for scenario_name, cap_s in cap.groupby("scenario"):
            GROUP_COLS = grouping_cols(scenario_name, cap_s)
            GROUP_COLS = keep_existing_cols(cap_s, GROUP_COLS)
            if not GROUP_COLS or "run" not in cap_s.columns:
                continue
            cap_s = cap_s.dropna(subset=GROUP_COLS)
            group_plus_run = GROUP_COLS + ["run"]

            cap_run_means = (
                cap_s
                .groupby(group_plus_run, dropna=True)["coverage"]
                .mean()
                .reset_index()
                .rename(columns={"coverage": "run_mean_coverage"})
            )

            for keys, g in cap_run_means.groupby(GROUP_COLS):
                if not isinstance(keys, tuple):
                    keys = (keys,)
                row = dict(zip(GROUP_COLS, keys))

                mc = mean_ci(g["run_mean_coverage"])
                row["cap_run_mean"] = mc["mean"]
                row["cap_run_ci_low"] = mc["ci_low"]
                row["cap_run_ci_high"] = mc["ci_high"]
                row["cap_run_std"] = mc["std"]
                row["cap_run_n"] = mc["n"]
                cap_run_rows.append(row)

    cap_run_out = pd.DataFrame(cap_run_rows)

    if not cap_node_out.empty:
        out = out.merge(cap_node_out, on=merge_keys_from(cap_node_out, out), how="left")
    if not cap_run_out.empty:
        out = out.merge(cap_run_out, on=merge_keys_from(cap_run_out, out), how="left")

out.to_csv(OUTPUT, index=False)
print(f"Aggregated results written to: {OUTPUT}")
