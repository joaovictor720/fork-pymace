import pathlib

def parse_network_overhead(run_dir: pathlib.Path):
    total_sent = 0
    total_recv = 0

    for log in run_dir.glob("node_*.log"):
        last = None
        with open(log) as f:
            for line in f:
                if "sent_bytes=" in line:
                    last = line

        if last is None:
            continue

        parts = [p.strip() for p in last.split(",")]
        for p in parts:
            if p.startswith("sent_bytes="):
                total_sent += int(p.split("=")[1])
            elif p.startswith("recv_bytes="):
                total_recv += int(p.split("=")[1])

    return {
        "network_overhead_bytes": total_sent + total_recv
    }
