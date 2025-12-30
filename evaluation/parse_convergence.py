import pathlib
from collections import defaultdict

def parse_convergence(run_dir: pathlib.Path):
    ops = defaultdict(lambda: {"create": None, "apply": []})

    for evlog in run_dir.glob("node_*.log.events"):
        with open(evlog) as f:
            for line in f:
                parts = [p.strip() for p in line.split(",")]
                ts = float(parts[0])

                data = {}
                for p in parts[1:]:
                    k, v = p.split("=")
                    data[k] = v

                op_id = data.get("op_id")
                if not op_id:
                    continue

                if data["event"] == "op_create":
                    ops[op_id]["create"] = ts
                elif data["event"] == "op_apply":
                    ops[op_id]["apply"].append(ts)

    convergence_times = []

    for op_id, d in ops.items():
        if d["create"] is None or not d["apply"]:
            continue
        convergence_times.append(max(d["apply"]) - d["create"])

    if not convergence_times:
        return {}

    return {
        "convergence_avg_s": sum(convergence_times) / len(convergence_times),
        "convergence_max_s": max(convergence_times),
    }
