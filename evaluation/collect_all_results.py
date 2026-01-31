import pandas as pd
from pathlib import Path

ROOT = Path("/home/mace/git/fork-pymace/results")


def _read_variant_scenario_params(variant_dir: Path):
    sc_path = variant_dir / "scenario.json"
    if not sc_path.exists():
        return {}

    try:
        import json
        sc = json.loads(sc_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    nodes_cfg = None
    area_x = None
    area_y = None

    try:
        nodes_cfg = int(sc.get("nodes", {}).get("count"))
    except Exception:
        nodes_cfg = None

    area_raw = sc.get("simulation", {}).get("area")
    if isinstance(area_raw, dict):
        try:
            area_x = float(area_raw.get("x"))
            area_y = float(area_raw.get("y"))
        except Exception:
            area_x = None
            area_y = None
    elif isinstance(area_raw, (list, tuple)) and len(area_raw) >= 2:
        try:
            area_x = float(area_raw[0])
            area_y = float(area_raw[1])
        except Exception:
            area_x = None
            area_y = None

    out = {}
    if nodes_cfg is not None:
        out["nodes_cfg"] = nodes_cfg

    if area_x is not None and area_y is not None and area_x > 0 and area_y > 0:
        out["area_x_m"] = area_x
        out["area_y_m"] = area_y
        area_km2 = (area_x * area_y) / 1_000_000.0
        out["area_km2"] = area_km2
        if nodes_cfg is not None and area_km2 > 0:
            out["density"] = float(nodes_cfg) / area_km2

    return out


rows = []
cap_rows = []

for scenario_dir in ROOT.glob("*__expanded"):
    scenario = scenario_dir.name.replace("__expanded", "")

    for variant_dir in scenario_dir.iterdir():
        if not variant_dir.is_dir():
            continue

        variant = variant_dir.name
        extra = _read_variant_scenario_params(variant_dir)

        for algo in ["broadcast", "rapid", "multiunicast"]:
            base = variant_dir / algo
            csv_path = base / "summary.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                df["scenario"] = scenario
                df["variant"] = variant
                df["algorithm"] = algo
                for k, v in extra.items():
                    df[k] = v
                rows.append(df)

            if base.exists() and base.is_dir():
                for run_dir in sorted(base.glob("run_*")):
                    cov_path = run_dir / "coverage_nodes.csv"
                    if not cov_path.exists():
                        continue

                    cdf = pd.read_csv(cov_path)
                    cdf["scenario"] = scenario
                    cdf["variant"] = variant
                    cdf["algorithm"] = algo
                    cdf["run"] = run_dir.name
                    for k, v in extra.items():
                        cdf[k] = v
                    cap_rows.append(cdf)

if rows:
    all_df = pd.concat(rows, ignore_index=True)

    # normaliza tipos
    for c in ["nodes_cfg", "area_x_m", "area_y_m", "area_km2", "density"]:
        if c in all_df.columns:
            all_df[c] = pd.to_numeric(all_df[c], errors="coerce")

    all_df.to_csv("all_results.csv", index=False)
    print("Saved all_results.csv")
else:
    print("No summary.csv files found; all_results.csv not created.")

if cap_rows:
    cap_df = pd.concat(cap_rows, ignore_index=True)
    if "coverage" in cap_df.columns:
        cap_df["coverage"] = pd.to_numeric(cap_df["coverage"], errors="coerce")

    for c in ["nodes_cfg", "area_x_m", "area_y_m", "area_km2", "density"]:
        if c in cap_df.columns:
            cap_df[c] = pd.to_numeric(cap_df[c], errors="coerce")

    cap_df.to_csv("all_capacity_samples.csv", index=False)
    print("Saved all_capacity_samples.csv")
else:
    print("No coverage_nodes.csv files found; all_capacity_samples.csv not created.")
