import json
import pandas as pd
from pathlib import Path

ROOT = Path("/home/mace/git/fork-pymace/results")
ROOT_REPO = Path("/home/mace/git/fork-pymace")
APPS_PATH = ROOT_REPO / "evaluation" / "apps.json"

cfg = json.loads(APPS_PATH.read_text(encoding="utf-8"))
apps = sorted(list(cfg.get("apps", {}).keys()))

rows = []
cap_rows = []

for scenario_dir in ROOT.glob("*__expanded"):
    scenario = scenario_dir.name.replace("__expanded", "")

    for variant_dir in scenario_dir.iterdir():
        if not variant_dir.is_dir():
            continue
        variant = variant_dir.name

        for app in apps:
            base = variant_dir / app
            csv_path = base / "summary.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                df["scenario"] = scenario
                df["variant"] = variant
                df["app"] = app
                rows.append(df)

            if base.exists() and base.is_dir():
                for run_dir in sorted(base.glob("run_*")):
                    cov_path = run_dir / "coverage_nodes.csv"
                    if not cov_path.exists():
                        continue
                    cdf = pd.read_csv(cov_path)
                    cdf["scenario"] = scenario
                    cdf["variant"] = variant
                    cdf["app"] = app
                    cdf["run"] = run_dir.name
                    cap_rows.append(cdf)

if rows:
    all_df = pd.concat(rows, ignore_index=True)
    all_df.to_csv("all_results.csv", index=False)
    print("Saved all_results.csv")
else:
    print("No summary.csv files found; all_results.csv not created.")

if cap_rows:
    cap_df = pd.concat(cap_rows, ignore_index=True)
    if "coverage" in cap_df.columns:
        cap_df["coverage"] = pd.to_numeric(cap_df["coverage"], errors="coerce")
    cap_df.to_csv("all_capacity_samples.csv", index=False)
    print("Saved all_capacity_samples.csv")
else:
    print("No coverage_nodes.csv files found; all_capacity_samples.csv not created.")
