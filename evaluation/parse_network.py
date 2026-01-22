# evaluation/parse_network.py
import pathlib
import csv

def _parse_netlog_counters(run_dir: pathlib.Path):
    total_tx = 0
    total_rx = 0
    nodes = 0

    for log in run_dir.glob("node_*.net.log"):
        tx_start = tx_end = None
        rx_start = rx_end = None

        with open(log) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BATMAN_TX_START") or line.startswith("RAPID_TX_START"):
                    tx_start = int(line.split("=", 1)[1])
                elif line.startswith("BATMAN_TX_END") or line.startswith("RAPID_TX_END"):
                    tx_end = int(line.split("=", 1)[1])
                elif line.startswith("BATMAN_RX_START") or line.startswith("RAPID_RX_START"):
                    rx_start = int(line.split("=", 1)[1])
                elif line.startswith("BATMAN_RX_END") or line.startswith("RAPID_RX_END"):
                    rx_end = int(line.split("=", 1)[1])

        if None in (tx_start, tx_end, rx_start, rx_end):
            continue

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

    # Mantém nomes do summary.csv, mas agora "packets" = frames do pcap filtrado
    return {
        "nodes": nodes,
        "total_tx_packets": None,
        "total_rx_packets": None,
        "total_packets": total_frames,
        "avg_tx_per_node": None,
        "avg_rx_per_node": None,
        "avg_packets_per_node": total_frames / nodes,
        "rx_to_tx_ratio": None,
        # Se você quiser usar depois, mas sem mudar o schema antigo:
        # "total_pcap_bytes": total_bytes,
    }

def parse_network_overhead(run_dir: pathlib.Path):
    # Fonte canônica: pcap_metrics.csv (frames L2 relevantes)
    pcap = _parse_pcap_metrics(run_dir)
    if pcap is not None:
        return pcap

    # Fallback: contadores antigos (se não houver pcap_metrics.csv)
    netlog = _parse_netlog_counters(run_dir)
    if netlog is not None:
        return netlog

    return {}
