import json
import sys
import itertools
import copy
from pathlib import Path

scenario_name = sys.argv[1]

root = Path(__file__).resolve().parent.parent
scenarios_dir = root / "scenarios"
base_dir = scenarios_dir / scenario_name
base_scenario_path = base_dir / "scenario.json"

with open(base_scenario_path) as f:
    base = json.load(f)

experiment = base.get("experiment")
if not experiment:
    print(scenario_name)
    sys.exit(0)

vary = experiment.get("vary", {})
vary_tuples = experiment.get("vary_tuples", [])

keys = list(vary.keys())
values = [vary[k] for k in keys]

expanded_root = scenarios_dir / f"{scenario_name}__expanded"
expanded_root.mkdir(exist_ok=True)

def value_to_str(v):
    if isinstance(v, list) or isinstance(v, tuple):
        if len(v) == 2:
            return f"{v[0]}-{v[1]}"
        return "-".join(str(x) for x in v)
    return str(v)

def set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value

variants = []

# -------------------------------------------------
# Caso 1: existe vary_tuples (parâmetros vinculados)
# -------------------------------------------------
if vary_tuples:
    for t in vary_tuples:
        base_variant = copy.deepcopy(base)
        base_variant.pop("experiment", None)

        tuple_tags = []

        # Aplica os valores vinculados
        for k, v in t.items():
            set_path(base_variant, k, v)
            tuple_tags.append(f"{k.split('.')[-1]}={value_to_str(v)}")

        # Se também houver "vary", aplicamos produto cartesiano sobre ele
        if vary:
            for combo in itertools.product(*values):
                sc = copy.deepcopy(base_variant)
                tags = list(tuple_tags)

                for k, v in zip(keys, combo):
                    set_path(sc, k, v)
                    tags.append(f"{k.split('.')[-1]}={value_to_str(v)}")

                variant_name = "__".join(tags)
                variant_dir = expanded_root / variant_name
                variant_dir.mkdir(parents=True, exist_ok=True)

                with open(variant_dir / "scenario.json", "w") as f:
                    json.dump(sc, f, indent=2)

                variants.append(f"{scenario_name}__expanded/{variant_name}")

        else:
            # Apenas a tupla, sem variações adicionais
            variant_name = "__".join(tuple_tags)
            variant_dir = expanded_root / variant_name
            variant_dir.mkdir(parents=True, exist_ok=True)

            with open(variant_dir / "scenario.json", "w") as f:
                json.dump(base_variant, f, indent=2)

            variants.append(f"{scenario_name}__expanded/{variant_name}")

# -------------------------------------------------
# Caso 2: apenas vary (comportamento antigo)
# -------------------------------------------------
elif vary:
    for combo in itertools.product(*values):
        sc = copy.deepcopy(base)
        sc.pop("experiment", None)

        tags = []
        for k, v in zip(keys, combo):
            set_path(sc, k, v)
            tags.append(f"{k.split('.')[-1]}={value_to_str(v)}")

        variant_name = "__join".join(tags)
        variant_dir = expanded_root / variant_name
        variant_dir.mkdir(parents=True, exist_ok=True)

        with open(variant_dir / "scenario.json", "w") as f:
            json.dump(sc, f, indent=2)

        variants.append(f"{scenario_name}__expanded/{variant_name}")

# -------------------------------------------------
# Caso 3: nada para variar
# -------------------------------------------------
else:
    print(scenario_name)
    sys.exit(0)

for v in variants:
    print(v)
