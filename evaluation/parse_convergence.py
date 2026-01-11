import pathlib

def parse_convergence(run_dir: pathlib.Path):
    # -----------------------------
    # 1. Find last op_create time
    # -----------------------------
    t_last_create = None

    for evlog in run_dir.glob("node_*.log.events"):
        with open(evlog) as f:
            for line in f:
                if "event=op_create" not in line:
                    continue
                ts = float(line.split(",")[0])
                if t_last_create is None or ts > t_last_create:
                    t_last_create = ts

    if t_last_create is None:
        return {}

    # -----------------------------
    # 2. Compute ground truth total
    #    = sum of final local values
    # -----------------------------
    final_locals = {}
    node_logs = list(run_dir.glob("node_*.log"))

    for log in node_logs:
        node_id = log.stem.split("_")[1]
        last_local = None

        with open(log) as f:
            for line in f:
                if "local=" not in line:
                    continue
                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )
                last_local = int(parts["local"])

        if last_local is not None:
            final_locals[node_id] = last_local

    if not final_locals:
        return {}

    ground_truth = sum(final_locals.values())

    # -----------------------------
    # 3. Find convergence time
    #    using op_apply events
    # -----------------------------
    node_conv_times = {}

    for evlog in run_dir.glob("node_*.log.events"):
        node_id = evlog.stem.split("_")[1]

        with open(evlog) as f:
            for line in f:
                if "event=op_apply" not in line:
                    continue

                ts = float(line.split(",")[0])
                if ts < t_last_create:
                    continue

                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )

                if int(parts.get("total", -1)) == ground_truth:
                    node_conv_times[node_id] = ts
                    break

    # If at least one node never converged → no convergence
    if len(node_conv_times) != len(node_logs):
        return {
            "last_op_create_s": t_last_create,
            "final_total": ground_truth,
            "converged": False,
            "convergence_time_s": "N/A",
        }

    t_conv = max(node_conv_times.values())

    return {
        "last_op_create_s": t_last_create,
        "final_total": ground_truth,
        "converged": True,
        "convergence_time_s": t_conv - t_last_create,
    }
