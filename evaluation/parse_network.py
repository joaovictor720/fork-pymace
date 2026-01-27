# evaluation/parse_network.py (versão generalizada completa)
import pathlib
import csv
import re
from typing import Dict, Optional, Tuple


_COUNTER_RE = re.compile(r"^([A-Z0-9]+)_(TX|RX)_(START|END)=(\d+)\s*$")


def _best_counter_set_from_netlog(netlog_path: pathlib.Path) -> Optional[Tuple[int, int, int, int]]:
    by_prefix: Dict[str, Dict[str, int]] = {}

    with netlog_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _COUNTER_RE.match(line.strip())
            if not m:
                continue
            prefix, direction, bound, value_s = m.groups()
            by_prefix.setdefault(prefix, {})[f"{direction}_{bound}"] = int(value_s)

    for prefix, d in by_prefix.items():
        if all(k in d for k in ("TX_START", "TX_END", "RX_START", "RX_END")):
            return d["TX_START"], d["TX_END"], d["RX_START"], d["RX_END"]

    return None


def _parse_netlog_counters(run_dir: pathlib.Path):
    total_tx = 0
    total_rx = 0
    nodes = 0

    for log in run_dir.glob("node_*.net.log"):
        counters = _best_counter_set_from_netlog(log)
        if counters is None:
            continue

        tx_start, tx_end, rx_start, rx_end = counters

        nodes += 1
        total_tx += (tx_end - tx_start)
        total_rx += (rx_end - rx_start)

    if nodes == 0:
        return None

    total_packets = total_tx + total_rx
    return {
        "nodes": nodes,
        "total_tx_packets": total_tx,
        "total_rx_packets": total_rx,
        "total_packets": total_packets,
        "avg_tx_per_node": total_tx / nodes,
        "avg_rx_per_node": total_rx / nodes,
        "avg_packets_per_node": total_packets / nodes,
        "rx_to_tx_ratio": (total_rx / total_tx) if total_tx > 0 else None,
    }


def _parse_pcap_metrics(run_dir: pathlib.Path):
    p = run_dir / "pcap_metrics.csv"
    if not p.exists():
        return None

    total_frames = 0
    total_bytes = 0
    nodes = 0

    with p.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("status") != "ok":
                continue
            try:
                frames = int(row["frames"])
                byt = int(row["bytes"])
            except Exception:
                continue
            nodes += 1
            total_frames += frames
            total_bytes += byt

    if nodes == 0:
        return None

    return {
        "nodes": nodes,
        "total_tx_packets": None,
        "total_rx_packets": None,
        "total_packets": total_frames,
        "avg_tx_per_node": None,
        "avg_rx_per_node": None,
        "avg_packets_per_node": total_frames / nodes,
        "rx_to_tx_ratio": None,
    }


def parse_network_overhead(run_dir: pathlib.Path):
    pcap = _parse_pcap_metrics(run_dir)
    if pcap is not None:
        return pcap

    netlog = _parse_netlog_counters(run_dir)
    if netlog is not None:
        return netlog

    return {}