import json
import sys
import itertools
import copy
from pathlib import Path
from typing import Any, Dict, List, Tuple

scenario_name = sys.argv[1]

root = Path(__file__).resolve().parent.parent
scenarios_dir = root / "scenarios"
base_dir = scenarios_dir / scenario_name
base_scenario_path = base_dir / "scenario.json"

with open(base_scenario_path, "r", encoding="utf-8") as f:
    base = json.load(f)

experiment = base.get("experiment")
if not experiment:
    print(scenario_name)
    sys.exit(0)

vary: Dict[str, List[Any]] = experiment.get("vary", {})
vary_tuples: List[Dict[str, Any]] = experiment.get("vary_tuples", [])

keys = list(vary.keys())
values = [vary[k] for k in keys]

expanded_root = scenarios_dir / f"{scenario_name}__expanded"
expanded_root.mkdir(exist_ok=True)

def value_to_str(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        if len(v) == 2:
            return f"{v[0]}-{v[1]}"
        return "-".join(str(x) for x in v)
    return str(v)

def set_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = obj
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value

def short_key(path: str) -> str:
    return path.split(".")[-1]

def write_variant(variant_dir: Path, scenario_obj: Dict[str, Any], params: Dict[str, Any], tags: List[str]) -> None:
    with open(variant_dir / "scenario.json", "w", encoding="utf-8") as f:
        json.dump(scenario_obj, f, indent=2)

    meta = {
        "scenario_base": scenario_name,
        "variant_name": variant_dir.name,
        "params": params,
        "tags": tags
    }
    with open(variant_dir / "variant_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

variants: List[str] = []

if vary_tuples:
    for t in vary_tuples:
        base_variant = copy.deepcopy(base)
        base_variant.pop("experiment", None)

        params: Dict[str, Any] = {}
        tuple_tags: List[str] = []

        for k, v in t.items():
            set_path(base_variant, k, v)
            params[k] = v
            tuple_tags.append(f"{short_key(k)}={value_to_str(v)}")

        if vary:
            for combo in itertools.product(*values):
                sc = copy.deepcopy(base_variant)
                tags = list(tuple_tags)
                params2 = dict(params)

                for k, v in zip(keys, combo):
                    set_path(sc, k, v)
                    params2[k] = v
                    tags.append(f"{short_key(k)}={value_to_str(v)}")

                variant_name = "__".join(tags)
                variant_dir = expanded_root / variant_name
                variant_dir.mkdir(parents=True, exist_ok=True)

                write_variant(variant_dir, sc, params2, tags)
                variants.append(f"{scenario_name}__expanded/{variant_name}")
        else:
            variant_name = "__".join(tuple_tags)
            variant_dir = expanded_root / variant_name
            variant_dir.mkdir(parents=True, exist_ok=True)

            write_variant(variant_dir, base_variant, params, tuple_tags)
            variants.append(f"{scenario_name}__expanded/{variant_name}")

elif vary:
    for combo in itertools.product(*values):
        sc = copy.deepcopy(base)
        sc.pop("experiment", None)

        tags: List[str] = []
        params: Dict[str, Any] = {}

        for k, v in zip(keys, combo):
            set_path(sc, k, v)
            params[k] = v
            tags.append(f"{short_key(k)}={value_to_str(v)}")

        variant_name = "__".join(tags)
        variant_dir = expanded_root / variant_name
        variant_dir.mkdir(parents=True, exist_ok=True)

        write_variant(variant_dir, sc, params, tags)
        variants.append(f"{scenario_name}__expanded/{variant_name}")

else:
    print(scenario_name)
    sys.exit(0)

for v in variants:
    print(v)
