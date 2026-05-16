#!/usr/bin/env python3
import argparse
import csv
import json
import pathlib
import re
import sys
from collections import Counter
from typing import Dict, Iterable


EVENT_PROTOCOL_MAP = {
    "rapid_data_send": "data",
    "rapid_gossip_send": "control",
    "rapid_request_send": "control",
    "rapid_heartbeat_send": "control",
    "trickle_summary_send": "control",
    "trickle_repair_send": "data",
}

RAPID_PCAP_TYPES = {
    "1": "data",
    "2": "control",
    "3": "control",
    "4": "control",
}

TRICKLE_PCAP_TYPES = {
    "1": "control",
    "2": "data",
}


def _parse_kv_line(line: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in line.strip().split(",")[1:]:
        part = raw.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _json_counter(value: str) -> Counter:
    if not value:
        return Counter()
    try:
        data = json.loads(value)
    except Exception:
        return Counter()
    if not isinstance(data, dict):
        return Counter()
    out: Counter = Counter()
    for key, raw in data.items():
        try:
            out[str(key)] += int(raw)
        except Exception:
            continue
    return out


def _iter_ok_pcap_rows(run_dir: pathlib.Path) -> Iterable[dict]:
    path = run_dir / "pcap_metrics.csv"
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") in {"ok", "ok_truncated"}:
                yield row


def _load_app(run_dir: pathlib.Path) -> str:
    status_path = run_dir / "run_status.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            app = data.get("app")
            if isinstance(app, str) and app:
                return app
        except Exception:
            pass

    for netlog in sorted(run_dir.glob("node_*.net.log")):
        try:
            with netlog.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("APP="):
                        return line.strip().split("=", 1)[1]
        except Exception:
            continue
    return ""


def _last_monitor_totals(run_dir: pathlib.Path) -> Dict[str, int]:
    totals = {
        "sent_msgs": 0,
        "recv_msgs": 0,
        "sent_bytes": 0,
        "recv_bytes": 0,
    }
    pattern = re.compile(r"\b(sent_msgs|recv_msgs|sent_bytes|recv_bytes)=(\d+)")

    for log in sorted(run_dir.glob("node_*.log")):
        last: Dict[str, int] = {}
        try:
            with log.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "sent_msgs=" not in line and "recv_msgs=" not in line:
                        continue
                    for key, value in pattern.findall(line):
                        last[key] = int(value)
        except Exception:
            continue
        for key in totals:
            totals[key] += last.get(key, 0)
    return totals


def _pcap_totals(run_dir: pathlib.Path) -> Dict[str, object]:
    frames = 0
    bytes_total = 0
    payload_types: Counter = Counter()
    protocol_types: Counter = Counter()
    protocol_groups: Counter = Counter()

    for row in _iter_ok_pcap_rows(run_dir):
        try:
            frames += int(row.get("frames") or 0)
            bytes_total += int(row.get("bytes") or 0)
        except Exception:
            pass
        payload_types.update(_json_counter(row.get("payload_type_frames_json", "")))
        protocol_types.update(_json_counter(row.get("protocol_type_frames_json", "")))
        protocol_groups.update(_json_counter(row.get("protocol_group_frames_json", "")))

    return {
        "link_frames": frames,
        "link_bytes": bytes_total,
        "payload_type_frames": payload_types,
        "protocol_type_frames": protocol_types,
        "protocol_group_frames": protocol_groups,
    }


def parse_message_counts(run_dir: pathlib.Path) -> Dict[str, object]:
    app = _load_app(run_dir)
    events: Counter = Counter()

    for evlog in sorted(run_dir.glob("node_*.log.events")):
        try:
            with evlog.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "event=" not in line:
                        continue
                    event = _parse_kv_line(line).get("event")
                    if event:
                        events[event] += 1
        except Exception:
            continue

    pcap = _pcap_totals(run_dir)
    monitor = _last_monitor_totals(run_dir)

    protocol_control = 0
    protocol_data = 0
    metric_scope = "app_events"

    for event, group in EVENT_PROTOCOL_MAP.items():
        if group == "control":
            protocol_control += events[event]
        elif group == "data":
            protocol_data += events[event]

    if protocol_control == 0 and protocol_data == 0:
        payload_types: Counter = pcap["payload_type_frames"]  # type: ignore[assignment]
        if app == "rapid":
            for msg_type, group in RAPID_PCAP_TYPES.items():
                if group == "control":
                    protocol_control += payload_types[msg_type]
                elif group == "data":
                    protocol_data += payload_types[msg_type]
            if protocol_control or protocol_data:
                metric_scope = "pcap_payload_type_fallback"
        elif app == "trickle":
            for msg_type, group in TRICKLE_PCAP_TYPES.items():
                if group == "control":
                    protocol_control += payload_types[msg_type]
                elif group == "data":
                    protocol_data += payload_types[msg_type]
            if protocol_control or protocol_data:
                metric_scope = "pcap_payload_type_fallback"

    batadv_control = 0
    batadv_data = 0
    batadv_unclassified = 0
    if app in {"broadcast", "multiunicast"}:
        protocol_groups: Counter = pcap["protocol_group_frames"]  # type: ignore[assignment]
        batadv_control = protocol_groups["control"]
        batadv_data = protocol_groups["data"] + protocol_groups["data_or_dat"]
        batadv_unclassified = protocol_groups["unclassified"]
        protocol_control = batadv_control
        protocol_data = batadv_data
        metric_scope = "batadv_frame_classification"

    app_sync_send = events["app_sync_send"]
    app_sync_recv = events["app_sync_recv"]
    if app in {"broadcast", "multiunicast"} and app_sync_send == 0:
        app_sync_send = monitor["sent_msgs"]
    if app in {"broadcast", "multiunicast"} and app_sync_recv == 0:
        app_sync_recv = monitor["recv_msgs"]

    protocol_type_frames: Counter = pcap["protocol_type_frames"]  # type: ignore[assignment]
    payload_type_frames: Counter = pcap["payload_type_frames"]  # type: ignore[assignment]

    out: Dict[str, object] = {
        "app": app,
        "message_metric_scope": metric_scope,
        "app_updates_created": events["op_create"],
        "app_sync_msgs": app_sync_send,
        "app_received_sync_msgs": app_sync_recv,
        "protocol_control_msgs": protocol_control,
        "protocol_data_msgs": protocol_data,
        "protocol_suppressed_events": events["trickle_suppressed"],
        "protocol_interval_reset_events": events["trickle_reset"],
        "batadv_control_frames": batadv_control,
        "batadv_data_frames": batadv_data,
        "batadv_unclassified_frames": batadv_unclassified,
        "link_frames": pcap["link_frames"],
        "link_bytes": pcap["link_bytes"],
        "unknown": batadv_unclassified,
        "monitor_sent_msgs": monitor["sent_msgs"],
        "monitor_recv_msgs": monitor["recv_msgs"],
        "event_counts_json": json.dumps(dict(sorted(events.items())), sort_keys=True),
        "payload_type_frames_json": json.dumps(dict(sorted(payload_type_frames.items())), sort_keys=True),
        "protocol_type_frames_json": json.dumps(dict(sorted(protocol_type_frames.items())), sort_keys=True),
    }
    return out


def write_run_files(run_dir: pathlib.Path, row: Dict[str, object]) -> None:
    json_path = run_dir / "message_counts.json"
    csv_path = run_dir / "message_counts.csv"
    json_path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="Run directory to parse")
    parser.add_argument("--write-run-files", action="store_true", help="Write message_counts.json/csv into run_dir")
    args = parser.parse_args()

    run_dir = pathlib.Path(args.run_dir)
    row = parse_message_counts(run_dir)
    if args.write_run_files:
        write_run_files(run_dir, row)
    print(json.dumps(row, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
