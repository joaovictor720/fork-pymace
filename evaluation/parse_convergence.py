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
    # 2. Find final total value
    # -----------------------------
    final_total = None
    node_logs = list(run_dir.glob("node_*.log"))

    for log in node_logs:
        with open(log) as f:
            for line in f:
                if "total=" not in line:
                    continue
                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )
                total = int(parts["total"])
                final_total = max(final_total or total, total)

    # -----------------------------
    # 3. Find convergence time per node
    # -----------------------------
    node_conv_times = []

    for log in node_logs:
        with open(log) as f:
            for line in f:
                ts = float(line.split(",")[0])
                if ts < t_last_create:
                    continue

                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )

                if int(parts["total"]) == final_total:
                    node_conv_times.append(ts)
                    break

    if not node_conv_times:
        return {}

    t_conv = max(node_conv_times)

    return {
        "last_op_create_s": t_last_create,
        "final_total": final_total,
        "convergence_time_s": t_conv - t_last_create,
    }
