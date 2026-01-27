import pathlib
import csv

def parse_convergence(run_dir: pathlib.Path):
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

    if not final_totals:
        return {}

    coverages = []
    abs_errors = []

    for node_id, tot in final_totals.items():
        if ground_truth > 0:
            coverages.append(tot / ground_truth)
        else:
            coverages.append(0.0)
        abs_errors.append(abs(ground_truth - tot))

    converged = all(t == ground_truth for t in final_totals.values())

    thresholds = {
        "90": 0.90 * ground_truth,
        "95": 0.95 * ground_truth,
        "99": 0.99 * ground_truth,
        "100": ground_truth
    }

    times = {k: {} for k in thresholds}

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
                tot_s = parts.get("total", "")
                try:
                    total = int(tot_s)
                except Exception:
                    continue

                for k, thr in thresholds.items():
                    if node_id not in times[k] and total >= thr:
                        times[k][node_id] = ts

    def agg_time(k):
        if len(times[k]) != len(final_totals):
            return None
        return max(times[k].values()) - t_last_create

    cov_path = run_dir / "coverage_nodes.csv"
    with cov_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node", "final_total_node", "ground_truth", "coverage"])
        for node_id in sorted(final_totals.keys(), key=lambda x: int(x) if x.isdigit() else x):
            tot = final_totals[node_id]
            cov = (tot / ground_truth) if ground_truth > 0 else 0.0
            w.writerow([node_id, tot, ground_truth, f"{cov:.12f}"])

    return {
        "last_op_create_s": t_last_create,
        "final_total": ground_truth,

        "avg_final_coverage": sum(coverages) / len(coverages) if coverages else None,
        "min_final_coverage": min(coverages) if coverages else None,
        "avg_abs_error": sum(abs_errors) / len(abs_errors) if abs_errors else None,
        "max_abs_error": max(abs_errors) if abs_errors else None,

        "time_to_90pct_s": agg_time("90"),
        "time_to_95pct_s": agg_time("95"),
        "time_to_99pct_s": agg_time("99"),
        "converged": converged,
        "convergence_time_s": agg_time("100"),
    }