import copy
import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT / "results"
SCENARIOS_ROOT = REPO_ROOT / "scenarios"


def _load_json(path: Path):
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _child_app_dirs(variant_dir: Path):
    try:
        return [p for p in variant_dir.iterdir() if p.is_dir()]
    except Exception:
        return []


def _find_metadata_file(variant_dir: Path, filename: str):
    direct = variant_dir / filename
    if direct.exists():
        return direct

    for app_dir in _child_app_dirs(variant_dir):
        candidate = app_dir / filename
        if candidate.exists():
            return candidate

    return None


def _apply_variant_params(base_scenario, variant_meta):
    if not isinstance(base_scenario, dict):
        return {}

    scenario = copy.deepcopy(base_scenario)
    params = variant_meta.get("params", {}) if isinstance(variant_meta, dict) else {}
    if not isinstance(params, dict):
        return scenario

    for dotted_key, value in params.items():
        parts = str(dotted_key).split(".")
        cursor = scenario
        valid_target = True

        for part in parts[:-1]:
            if not isinstance(cursor, dict):
                valid_target = False
                break
            if part not in cursor or not isinstance(cursor[part], dict):
                cursor[part] = {}
            cursor = cursor[part]

        if valid_target and isinstance(cursor, dict) and parts:
            cursor[parts[-1]] = value

    return scenario


def _load_variant_scenario(variant_dir: Path):
    scenario_path = _find_metadata_file(variant_dir, "scenario.json")
    scenario = _load_json(scenario_path) if scenario_path is not None else None
    if isinstance(scenario, dict):
        return scenario

    variant_meta_path = _find_metadata_file(variant_dir, "variant_meta.json")
    variant_meta = _load_json(variant_meta_path) if variant_meta_path is not None else None
    if not isinstance(variant_meta, dict):
        return {}

    scenario_base = variant_meta.get("scenario_base")
    if not scenario_base:
        return {}

    base_scenario = _load_json(SCENARIOS_ROOT / str(scenario_base) / "scenario.json")
    if not isinstance(base_scenario, dict):
        return {}

    return _apply_variant_params(base_scenario, variant_meta)


def _read_variant_scenario_params(variant_dir: Path):
    sc = _load_variant_scenario(variant_dir)
    if not isinstance(sc, dict):
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

    metadata = sc.get("metadata", {})
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[f"meta_{key}"] = value

    node_cfg = sc.get("node_config", {})
    if isinstance(node_cfg, dict):
        for key, value in node_cfg.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[f"cfg_{key}"] = value

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

for scenario_dir in ROOT.rglob("*__expanded"):
    if not scenario_dir.is_dir():
        continue
    scenario = scenario_dir.name.replace("__expanded", "")

    for variant_dir in scenario_dir.iterdir():
        if not variant_dir.is_dir():
            continue

        variant = variant_dir.name
        extra = _read_variant_scenario_params(variant_dir)

        for algo in ["broadcast", "rapid", "multiunicast", "trickle"]:
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

    for c in [c for c in all_df.columns if c.startswith("cfg_")]:
        all_df[c] = pd.to_numeric(all_df[c], errors="ignore")

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

    for c in [c for c in cap_df.columns if c.startswith("cfg_")]:
        cap_df[c] = pd.to_numeric(cap_df[c], errors="ignore")

    cap_df.to_csv("all_capacity_samples.csv", index=False)
    print("Saved all_capacity_samples.csv")
else:
    print("No coverage_nodes.csv files found; all_capacity_samples.csv not created.")
