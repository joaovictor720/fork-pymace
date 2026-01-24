import pandas as pd
import numpy as np
from scipy import stats
import sys
from pathlib import Path
from typing import List

INPUT = sys.argv[1]
OUTPUT = sys.argv[2]

df = pd.read_csv(INPUT)

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

PARAM_CANDIDATES = ["count", "nodes", "ops_per_sec", "diss_per_sec", "duration", "cooldown"]

def choose_group_params(df_sub: pd.DataFrame) -> List[str]:
    cols = []
    for c in PARAM_CANDIDATES:
        if c in df_sub.columns:
            nuniq = pd.to_numeric(df_sub[c], errors="coerce").dropna().nunique()
            if nuniq > 1:
                cols.append(c)
    if not cols:
        for c in ["count", "nodes", "ops_per_sec"]:
            if c in df_sub.columns:
                cols.append(c)
                break
    return cols

rows = []

group_top = []
if "scenario" in df.columns:
    group_top.append("scenario")
if "app" in df.columns:
    group_top.append("app")
elif "algorithm" in df.columns:
    df = df.rename(columns={"algorithm": "app"})
    group_top.append("app")

if not group_top:
    raise SystemExit("[ERROR] Expected at least scenario/app columns in input CSV.")

for keys_top, df_sa in df.groupby(group_top, dropna=True):
    if not isinstance(keys_top, tuple):
        keys_top = (keys_top,)
    top_row = dict(zip(group_top, keys_top))

    group_params = choose_group_params(df_sa)
    GROUP_COLS = group_top + group_params

    df_sa = df_sa.dropna(subset=GROUP_COLS)

    for keys, g in df_sa.groupby(GROUP_COLS, dropna=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(GROUP_COLS, keys))

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

        rows.append(row)

out = pd.DataFrame(rows)

cap_path = Path(INPUT).with_name("all_capacity_samples.csv")
if cap_path.exists():
    cap = pd.read_csv(cap_path)
    if "algorithm" in cap.columns and "app" not in cap.columns:
        cap = cap.rename(columns={"algorithm": "app"})

    cap["coverage"] = pd.to_numeric(cap.get("coverage"), errors="coerce")

    base_keys = [k for k in ["scenario", "app"] if k in cap.columns]

    for scenario_app, cap_sa in cap.groupby(base_keys, dropna=True):
        if not isinstance(scenario_app, tuple):
            scenario_app = (scenario_app,)
        group_params = choose_group_params(cap_sa)
        GROUP_COLS = base_keys + group_params

        cap_sa = cap_sa.dropna(subset=GROUP_COLS)

        cap_node_rows = []
        for keys, g in cap_sa.groupby(GROUP_COLS, dropna=True):
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

        if cap_node_rows:
            cap_node_out = pd.DataFrame(cap_node_rows)
            merge_keys = [k for k in cap_node_out.columns if k in out.columns and k not in ["cap_node_mean","cap_node_ci_low","cap_node_ci_high","cap_node_std","cap_node_n"]]
            out = out.merge(cap_node_out, on=merge_keys, how="left")

    if "run" in cap.columns:
        for scenario_app, cap_sa in cap.groupby(base_keys, dropna=True):
            if not isinstance(scenario_app, tuple):
                scenario_app = (scenario_app,)
            group_params = choose_group_params(cap_sa)
            GROUP_COLS = base_keys + group_params
            cap_sa = cap_sa.dropna(subset=GROUP_COLS)

            group_plus_run = GROUP_COLS + ["run"]
            cap_run_means = (
                cap_sa
                .groupby(group_plus_run, dropna=True)["coverage"]
                .mean()
                .reset_index()
                .rename(columns={"coverage": "run_mean_coverage"})
            )

            cap_run_rows = []
            for keys, g in cap_run_means.groupby(GROUP_COLS, dropna=True):
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

            if cap_run_rows:
                cap_run_out = pd.DataFrame(cap_run_rows)
                merge_keys = [k for k in cap_run_out.columns if k in out.columns and k not in ["cap_run_mean","cap_run_ci_low","cap_run_ci_high","cap_run_std","cap_run_n"]]
                out = out.merge(cap_run_out, on=merge_keys, how="left")

out.to_csv(OUTPUT, index=False)
print(f"Aggregated results written to: {OUTPUT}")
