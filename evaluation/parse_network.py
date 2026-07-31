# evaluation/parse_network.py (versão generalizada completa)
import pathlib
import csv
import json
import re
from typing import Dict, List, Optional, Tuple


_COUNTER_RE = re.compile(r"^([A-Z0-9]+)_(TX|RX)_(START|END)=(\d+)\s*$")

# Keep the same row selection used by the existing total-packets metric.  The
# breakdown must classify this exact set; it must not silently gain or lose
# frames relative to total_packets.
_PCAP_TOTAL_OK_STATUSES = {"ok"}
_BREAKDOWN_OK_STATUSES = {"ok", "warning_unclassified"}


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
        "network_overhead_source": "netlog",
        "packet_breakdown_source": None,
        "classification_status": "unavailable",
        "packet_breakdown_warning": False,
    }


def _optional_int(row: Dict[str, str], key: str) -> Optional[int]:
    value = row.get(key)
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _json_value(value: Optional[str]):
    if value is None or not str(value).strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _diagnostics_by_node(rows: List[Dict[str, str]], field: str) -> Optional[str]:
    diagnostics = {}
    for row in rows:
        value = _json_value(row.get(field))
        if value in (None, {}, []):
            continue
        diagnostics[row.get("node", "unknown")] = value
    if not diagnostics:
        return None
    return json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))


def _parse_pcap_metrics(run_dir: pathlib.Path):
    p = run_dir / "pcap_metrics.csv"
    if not p.exists():
        return None

    total_frames = 0
    total_bytes = 0
    nodes = 0
    selected_rows: List[Dict[str, str]] = []
    pcap_fieldnames = set()

    with p.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        pcap_fieldnames = set(r.fieldnames or [])
        for row in r:
            if row.get("status") not in _PCAP_TOTAL_OK_STATUSES:
                continue
            try:
                frames = int(row["frames"])
                byt = int(row["bytes"])
            except Exception:
                continue
            nodes += 1
            total_frames += frames
            total_bytes += byt
            selected_rows.append(row)

    if nodes == 0:
        return None

    result = {
        "nodes": nodes,
        "total_tx_packets": None,
        "total_rx_packets": None,
        "total_packets": total_frames,
        "avg_tx_per_node": None,
        "avg_rx_per_node": None,
        "avg_packets_per_node": total_frames / nodes,
        "rx_to_tx_ratio": None,
        "network_overhead_source": "pcap_metrics",
        "packet_breakdown_source": "pcap_metrics",
    }

    category_fields = ("payload_frames", "control_frames", "unclassified_frames")
    if not set(category_fields).issubset(pcap_fieldnames):
        result.update({
            "classification_status": "unavailable",
            "packet_breakdown_warning": False,
            "packet_breakdown_unavailable_reason": "legacy_pcap_metrics_without_breakdown",
        })
        return result

    breakdown_available = True
    breakdown_invalid = False
    warning_unclassified = False
    unavailable_reasons = []
    residual_mismatches = []
    payload_frames = 0
    control_frames = 0
    unclassified_frames = 0

    for row in selected_rows:
        node = row.get("node", "unknown")
        values = [_optional_int(row, field) for field in category_fields]
        if any(value is None for value in values):
            breakdown_available = False
            unavailable_reasons.append(f"{node}:missing_category_counts")
            continue

        payload, control, unclassified = values
        assert payload is not None and control is not None and unclassified is not None
        if payload < 0 or control < 0 or unclassified < 0:
            breakdown_available = False
            unavailable_reasons.append(f"{node}:negative_category_count")
            continue

        payload_frames += payload
        control_frames += control
        unclassified_frames += unclassified

        frames = _optional_int(row, "frames")
        assert frames is not None
        computed_residual = frames - payload - control - unclassified
        reported_residual = _optional_int(row, "classification_residual")
        # Tolerate an early spelling used while developing the classifier.
        if reported_residual is None:
            reported_residual = _optional_int(row, "accounting_residual")

        if reported_residual is not None and reported_residual != computed_residual:
            breakdown_invalid = True
            residual_mismatches.append(
                f"{node}:reported={reported_residual},computed={computed_residual}"
            )
        if computed_residual != 0 or (reported_residual not in (None, 0)):
            breakdown_invalid = True

        status = str(row.get("classification_status", "")).strip().lower()
        if not status:
            # If all counts exist, the accounting itself is enough to support
            # files produced by short-lived development versions.
            status = "warning_unclassified" if unclassified > 0 else "ok"
        if status == "unavailable":
            breakdown_available = False
            unavailable_reasons.append(f"{node}:classification_unavailable")
        elif status == "error_classification_residual":
            breakdown_invalid = True
        elif status not in _BREAKDOWN_OK_STATUSES:
            breakdown_available = False
            unavailable_reasons.append(f"{node}:unknown_status={status}")

        if unclassified > 0 or status == "warning_unclassified":
            warning_unclassified = True

    if not breakdown_available:
        result.update({
            "classification_status": "unavailable",
            "packet_breakdown_warning": False,
            "packet_breakdown_unavailable_reason": ";".join(unavailable_reasons),
        })
        return result

    classification_residual = (
        total_frames - payload_frames - control_frames - unclassified_frames
    )
    if classification_residual != 0:
        breakdown_invalid = True

    if breakdown_invalid:
        classification_status = "error_classification_residual"
    elif warning_unclassified:
        classification_status = "warning_unclassified"
    else:
        classification_status = "ok"

    result.update({
        "total_payload_packets": payload_frames,
        "total_control_packets": control_frames,
        "total_unclassified_packets": unclassified_frames,
        "avg_payload_per_node": payload_frames / nodes,
        "avg_control_per_node": control_frames / nodes,
        "avg_unclassified_per_node": unclassified_frames / nodes,
        "classification_residual": classification_residual,
        "classification_status": classification_status,
        "packet_breakdown_warning": warning_unclassified,
        "unclassified_reasons_json": _diagnostics_by_node(
            selected_rows, "unclassified_reasons_json"
        ),
        "unclassified_sample_frames_json": _diagnostics_by_node(
            selected_rows, "unclassified_sample_frames_json"
        ),
    })
    if residual_mismatches:
        result["classification_residual_mismatches"] = ";".join(residual_mismatches)
    return result


def parse_network_overhead(run_dir: pathlib.Path):
    pcap = _parse_pcap_metrics(run_dir)
    if pcap is not None:
        return pcap

    netlog = _parse_netlog_counters(run_dir)
    if netlog is not None:
        return netlog

    return {}
