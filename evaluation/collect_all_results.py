import pandas as pd
from pathlib import Path

ROOT = Path("/home/mace/git/fork-pymace/results")

rows = []

for scenario_dir in ROOT.glob("*__expanded"):
    scenario = scenario_dir.name.replace("__expanded", "")

    for variant_dir in scenario_dir.iterdir():
        if not variant_dir.is_dir():
            continue
        variant = variant_dir.name

        for algo in ["broadcast", "rapid", "multiunicast"]:
            csv_path = variant_dir / algo / "summary.csv"
            if not csv_path.exists():
                continue

            df = pd.read_csv(csv_path)
            df["scenario"] = scenario
            df["variant"] = variant
            df["algorithm"] = algo

            rows.append(df)

all_df = pd.concat(rows, ignore_index=True)
all_df.to_csv("all_results.csv", index=False)

print("Saved all_results.csv")
