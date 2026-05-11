import pathlib
import re
from typing import Dict


_EVENT_RE = re.compile(r"\bevent=([^,]+)")
_BYTES_RE = re.compile(r"\bbytes=(\d+)")


def _event_name(line: str):
    m = _EVENT_RE.search(line)
    if not m:
        return None
    return m.group(1).strip()


def _event_bytes(line: str) -> int:
    m = _BYTES_RE.search(line)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def parse_trickle_events(run_dir: pathlib.Path) -> Dict[str, object]:
    event_logs = list(run_dir.glob("node_*.log.events"))
    if not event_logs:
        return {}

    counts = {
        "trickle_summary_send": 0,
        "trickle_summary_recv": 0,
        "trickle_repair_send": 0,
        "trickle_repair_recv": 0,
        "trickle_suppressed": 0,
        "trickle_reset": 0,
        "trickle_interval_change": 0,
    }
    bytes_by_event = {
        "trickle_summary_send": 0,
        "trickle_repair_send": 0,
    }

    for evlog in event_logs:
        with evlog.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                event = _event_name(line)
                if event not in counts:
                    continue

                counts[event] += 1
                if event in bytes_by_event:
                    bytes_by_event[event] += _event_bytes(line)

    nodes = len(event_logs)
    result: Dict[str, object] = {"trickle_event_nodes": nodes}

    for event, count in counts.items():
        result[event] = count
        result[f"{event}_per_node"] = count / nodes if nodes else None

    result["trickle_update_send"] = counts["trickle_repair_send"]
    result["trickle_update_recv"] = counts["trickle_repair_recv"]
    result["trickle_update_send_per_node"] = counts["trickle_repair_send"] / nodes if nodes else None
    result["trickle_update_recv_per_node"] = counts["trickle_repair_recv"] / nodes if nodes else None

    for event, total_bytes in bytes_by_event.items():
        result[f"{event}_bytes"] = total_bytes
        result[f"{event}_bytes_per_node"] = total_bytes / nodes if nodes else None

    result["trickle_update_send_bytes"] = bytes_by_event["trickle_repair_send"]
    result["trickle_update_send_bytes_per_node"] = (
        bytes_by_event["trickle_repair_send"] / nodes if nodes else None
    )

    return result
