# evaluation/parse_convergence.py
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
    # 3. Read final totals
    # -----------------------------
    final_totals = {}
    for log in node_logs:
        node_id = log.stem.split("_")[1]
        last_total = None
        with open(log) as f:
            for line in f:
                if "total=" not in line:
                    continue
                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )
                last_total = int(parts["total"])
        if last_total is not None:
            final_totals[node_id] = last_total

    coverages = [t / ground_truth for t in final_totals.values()]
    abs_errors = [abs(ground_truth - t) for t in final_totals.values()]

    converged = all(t == ground_truth for t in final_totals.values())

    # -----------------------------
    # 4. Convergence thresholds
    # -----------------------------
    thresholds = {
        "90": 0.90 * ground_truth,
        "95": 0.95 * ground_truth,
        "99": 0.99 * ground_truth,
        "100": ground_truth
    }

    times = {k: {} for k in thresholds}

    # Scan op_apply events in time order
    for evlog in run_dir.glob("node_*.log.events"):
        node_id = evlog.stem.split("_")[1]

        with open(evlog) as f:
            for line in f:
                if "event=op_apply" not in line:
                    continue

                ts = float(line.split(",")[0])
                parts = dict(
                    p.split("=") for p in
                    (x.strip() for x in line.strip().split(",")[1:])
                )
                total = int(parts.get("total", -1))

                for k, thr in thresholds.items():
                    if node_id not in times[k] and total >= thr:
                        times[k][node_id] = ts

    # -----------------------------
    # 5. Aggregate times
    # -----------------------------
    def agg_time(k):
        if len(times[k]) != len(final_totals):
            return None
        return max(times[k].values()) - t_last_create

    return {
        "last_op_create_s": t_last_create,
        "final_total": ground_truth,

        "avg_final_coverage": sum(coverages) / len(coverages),
        "min_final_coverage": min(coverages),
        "avg_abs_error": sum(abs_errors) / len(abs_errors),
        "max_abs_error": max(abs_errors),

        "time_to_90pct_s": agg_time("90"),
        "time_to_95pct_s": agg_time("95"),
        "time_to_99pct_s": agg_time("99"),
        "converged": converged,
        "convergence_time_s": agg_time("100"),
    }
