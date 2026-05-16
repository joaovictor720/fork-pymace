#!/usr/bin/env python3
import argparse
import csv
import glob
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _parse_tcpdump_stderr(path: Path) -> Dict[str, int]:
    out = {"captured": 0, "filtered": 0, "dropped": 0}
    if not path.exists():
        return out
    txt = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(\d+)\s+packets captured", txt)
    if m:
        out["captured"] = int(m.group(1))
    m = re.search(r"(\d+)\s+packets received by filter", txt)
    if m:
        out["filtered"] = int(m.group(1))
    m = re.search(r"(\d+)\s+packets dropped by kernel", txt)
    if m:
        out["dropped"] = int(m.group(1))
    return out


def _load_apps_cfg(root: Path) -> Dict[str, object]:
    p = root / "evaluation" / "apps.json"
    return json.loads(p.read_text(encoding="utf-8"))


def display_filter_for_app(app: str, apps_cfg: Dict[str, object]) -> str:
    apps = apps_cfg.get("apps", {})
    if not isinstance(apps, dict) or app not in apps:
        raise ValueError(f"App not found in apps.json: {app}")
    cfg = apps[app]
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid app entry in apps.json: {app}")
    df = cfg.get("tshark_display_filter", "")
    if not isinstance(df, str) or not df.strip():
        raise ValueError(f"Missing tshark_display_filter for app={app} in apps.json")
    return df.strip()


def payload_type_mode_for_app(app: str, apps_cfg: Dict[str, object]) -> str:
    apps = apps_cfg.get("apps", {})
    if not isinstance(apps, dict) or app not in apps:
        return "none"
    cfg = apps[app]
    if not isinstance(cfg, dict):
        return "none"
    mode = cfg.get("payload_type_mode", "none")
    if not isinstance(mode, str):
        return "none"
    return mode.strip().lower()


@dataclass
class PcapStats:
    frames: int = 0
    bytes_total: int = 0
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    min_len: Optional[int] = None
    max_len: Optional[int] = None
    payload_type_frames: Dict[int, int] = None  # type: ignore
    payload_type_bytes: Dict[int, int] = None   # type: ignore
    protocol_type_frames: Dict[str, int] = None  # type: ignore
    protocol_type_bytes: Dict[str, int] = None   # type: ignore
    protocol_group_frames: Dict[str, int] = None  # type: ignore
    protocol_group_bytes: Dict[str, int] = None   # type: ignore

    def __post_init__(self) -> None:
        self.payload_type_frames = {}
        self.payload_type_bytes = {}
        self.protocol_type_frames = {}
        self.protocol_type_bytes = {}
        self.protocol_group_frames = {}
        self.protocol_group_bytes = {}

    @property
    def duration_sec(self) -> float:
        if self.frames <= 0 or self.first_ts is None or self.last_ts is None:
            return 0.0
        return max(0.0, self.last_ts - self.first_ts)

    @property
    def mean_len(self) -> float:
        if self.frames <= 0:
            return 0.0
        return self.bytes_total / float(self.frames)

    @property
    def pps(self) -> float:
        duration = self.duration_sec
        if duration <= 0.0:
            return 0.0
        return self.frames / duration

    @property
    def bps(self) -> float:
        duration = self.duration_sec
        if duration <= 0.0:
            return 0.0
        return (self.bytes_total * 8.0) / duration


def _tshark_fields(payload_mode: str) -> List[str]:
    fields = ["frame.time_epoch", "frame.len"]
    if payload_mode == "first_byte_hex":
        fields.append("data.data")
    elif payload_mode == "batadv_packet_type":
        fields.append("batadv.batman.packet_type")
        fields.append("batadv.unicast_4addr.subtype")
    return fields


def _run_tshark_rows(pcap_path: str, dfilt: str, fields: List[str]) -> subprocess.Popen:
    cmd = ["tshark", "-r", pcap_path, "-Y", dfilt, "-T", "fields", "-E", "separator=\t"]
    for field in fields:
        cmd += ["-e", field]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _first_byte_from_hex(data_data: str) -> Optional[int]:
    if not data_data:
        return None
    hex_data = data_data.strip()
    if not hex_data:
        return None
    if ":" in hex_data:
        hex_data = hex_data.replace(":", "")
    if len(hex_data) < 2:
        return None
    try:
        return int(hex_data[0:2], 16)
    except ValueError:
        return None


BATADV_TYPE_NAMES = {
    0x00: "BATADV_IV_OGM",
    0x01: "BATADV_BCAST",
    0x02: "BATADV_CODED",
    0x03: "BATADV_ELP",
    0x04: "BATADV_OGM2",
    0x05: "BATADV_MCAST",
    0x40: "BATADV_UNICAST",
    0x41: "BATADV_UNICAST_FRAG",
    0x42: "BATADV_UNICAST_4ADDR",
    0x43: "BATADV_ICMP",
    0x44: "BATADV_UNICAST_TVLV",
}

BATADV_CONTROL_TYPES = {
    "BATADV_IV_OGM",
    "BATADV_ELP",
    "BATADV_OGM2",
    "BATADV_ICMP",
    "BATADV_UNICAST_TVLV",
}

BATADV_DATA_TYPES = {
    "BATADV_BCAST",
    "BATADV_CODED",
    "BATADV_MCAST",
    "BATADV_UNICAST",
    "BATADV_UNICAST_FRAG",
}

BATADV_4ADDR_DATA_SUBTYPE = 0x01
BATADV_4ADDR_CONTROL_SUBTYPES = {0x02, 0x03, 0x04}


def _first_int_field(value: str) -> Optional[int]:
    if not value:
        return None
    for part in re.split(r"[,; ]+", value.strip()):
        if not part:
            continue
        try:
            return int(part, 0)
        except ValueError:
            continue
    return None


def _batadv_type_name(packet_type: int, subtype: Optional[int]) -> str:
    base = BATADV_TYPE_NAMES.get(packet_type)
    if base is None:
        return f"BATADV_RESERVED_0x{packet_type:02x}"
    if base != "BATADV_UNICAST_4ADDR" or subtype is None:
        return base
    if subtype == BATADV_4ADDR_DATA_SUBTYPE:
        return "BATADV_UNICAST_4ADDR_DATA"
    if subtype in BATADV_4ADDR_CONTROL_SUBTYPES:
        return f"BATADV_UNICAST_4ADDR_DAT_{subtype}"
    return f"BATADV_UNICAST_4ADDR_SUBTYPE_{subtype}"


def _batadv_group(type_name: str) -> str:
    if type_name in BATADV_CONTROL_TYPES or type_name.startswith("BATADV_UNICAST_4ADDR_DAT_"):
        return "control"
    if (
        type_name in BATADV_DATA_TYPES
        or type_name == "BATADV_UNICAST_4ADDR_DATA"
        or type_name.startswith("BATADV_UNICAST_4ADDR_SUBTYPE_")
    ):
        return "data"
    if type_name == "BATADV_UNICAST_4ADDR":
        return "data_or_dat"
    return "unclassified"


def _iter_pcap_frames(pcap_path: str):
    try:
        with open(pcap_path, "rb") as f:
            header = f.read(24)
            if len(header) < 24:
                return

            magic = header[:4]
            if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
                endian = "<"
            elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
                endian = ">"
            else:
                return

            linktype = struct.unpack(endian + "I", header[20:24])[0]

            while True:
                rec_header = f.read(16)
                if len(rec_header) == 0:
                    return
                if len(rec_header) < 16:
                    return
                _ts_sec, _ts_frac, incl_len, _orig_len = struct.unpack(endian + "IIII", rec_header)
                frame = f.read(incl_len)
                if len(frame) < incl_len:
                    return
                yield linktype, frame
    except OSError:
        return


def _batadv_payload_offset(linktype: int, frame: bytes) -> Optional[int]:
    # LINKTYPE_ETHERNET
    if linktype == 1:
        if len(frame) < 14:
            return None
        offset = 12
        eth_type = int.from_bytes(frame[offset:offset + 2], "big")
        offset += 2
        while eth_type in (0x8100, 0x88A8, 0x9100):
            if len(frame) < offset + 4:
                return None
            eth_type = int.from_bytes(frame[offset + 2:offset + 4], "big")
            offset += 4
        if eth_type != 0x4305:
            return None
        return offset

    # LINKTYPE_LINUX_SLL
    if linktype == 113:
        if len(frame) < 16:
            return None
        eth_type = int.from_bytes(frame[14:16], "big")
        return 16 if eth_type == 0x4305 else None

    # LINKTYPE_LINUX_SLL2
    if linktype == 276:
        if len(frame) < 20:
            return None
        eth_type = int.from_bytes(frame[0:2], "big")
        return 20 if eth_type == 0x4305 else None

    return None


def _batadv_counts_from_pcap(pcap_path: str) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
    type_frames: Dict[str, int] = {}
    group_frames: Dict[str, int] = {}
    type_bytes: Dict[str, int] = {}
    group_bytes: Dict[str, int] = {}

    for linktype, frame in _iter_pcap_frames(pcap_path):
        payload_offset = _batadv_payload_offset(linktype, frame)
        if payload_offset is None or len(frame) <= payload_offset:
            continue

        packet_type = frame[payload_offset]
        subtype = None
        if packet_type == 0x42 and len(frame) > payload_offset + 16:
            subtype = frame[payload_offset + 16]

        type_name = _batadv_type_name(packet_type, subtype)
        group = _batadv_group(type_name)
        type_frames[type_name] = type_frames.get(type_name, 0) + 1
        group_frames[group] = group_frames.get(group, 0) + 1
        type_bytes[type_name] = type_bytes.get(type_name, 0) + len(frame)
        group_bytes[group] = group_bytes.get(group, 0) + len(frame)

    return type_frames, type_bytes, group_frames, group_bytes


def compute_metrics_allow_partial(pcap_path: str, dfilt: str, payload_mode: str) -> Tuple[PcapStats, int, str]:
    try:
        proc = _run_tshark_rows(pcap_path, dfilt, _tshark_fields(payload_mode))
    except Exception as exc:
        return PcapStats(), 127, str(exc)

    stats = PcapStats()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue

        ts_s = parts[0].strip()
        ln_s = parts[1].strip()

        try:
            ts = float(ts_s)
            ln = int(ln_s)
        except ValueError:
            continue

        stats.frames += 1
        stats.bytes_total += ln

        if stats.first_ts is None:
            stats.first_ts = ts
        stats.last_ts = ts

        if stats.min_len is None or ln < stats.min_len:
            stats.min_len = ln
        if stats.max_len is None or ln > stats.max_len:
            stats.max_len = ln

        if payload_mode == "first_byte_hex":
            data_data = parts[2].strip() if len(parts) > 2 else ""
            message_type = _first_byte_from_hex(data_data)
            if message_type is not None:
                stats.payload_type_frames[message_type] = stats.payload_type_frames.get(message_type, 0) + 1
                stats.payload_type_bytes[message_type] = stats.payload_type_bytes.get(message_type, 0) + ln
        elif payload_mode == "batadv_packet_type":
            packet_type = _first_int_field(parts[2].strip() if len(parts) > 2 else "")
            subtype = _first_int_field(parts[3].strip() if len(parts) > 3 else "")
            if packet_type is not None:
                type_name = _batadv_type_name(packet_type, subtype)
                group = _batadv_group(type_name)
                stats.protocol_type_frames[type_name] = stats.protocol_type_frames.get(type_name, 0) + 1
                stats.protocol_type_bytes[type_name] = stats.protocol_type_bytes.get(type_name, 0) + ln
                stats.protocol_group_frames[group] = stats.protocol_group_frames.get(group, 0) + 1
                stats.protocol_group_bytes[group] = stats.protocol_group_bytes.get(group, 0) + ln

    assert proc.stderr is not None
    stderr = proc.stderr.read()
    rc = proc.wait()

    if payload_mode == "batadv_packet_type":
        raw_type_frames, raw_type_bytes, raw_group_frames, raw_group_bytes = _batadv_counts_from_pcap(pcap_path)
        if raw_type_frames:
            stats.protocol_type_frames = raw_type_frames
            stats.protocol_type_bytes = raw_type_bytes
            stats.protocol_group_frames = raw_group_frames
            stats.protocol_group_bytes = raw_group_bytes

    return stats, rc, stderr.strip()


def append_to_netlog(netlog_path: Path, payload: Dict[str, object]) -> None:
    with netlog_path.open("a", encoding="utf-8") as f:
        for key, value in payload.items():
            if isinstance(value, float):
                f.write(f"{key}={value:.6f}\n")
            else:
                f.write(f"{key}={value}\n")


def write_summary(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result_dir", help="Run directory (e.g. .../broadcast/run_001/)")
    ap.add_argument("app", help="App name (must exist in evaluation/apps.json)")
    ap.add_argument("--delete", action="store_true", help="Delete pcap after successful processing")
    ap.add_argument("--append-netlog", action="store_true", help="Append PCAP_* fields to node_*.net.log")
    args = ap.parse_args()

    result_dir = Path(args.result_dir)
    if not result_dir.exists():
        raise SystemExit(f"[ERROR] result_dir not found: {result_dir}")

    root = Path(__file__).resolve().parent.parent
    apps_cfg = _load_apps_cfg(root)

    dfilt = display_filter_for_app(args.app, apps_cfg)
    payload_mode = payload_type_mode_for_app(args.app, apps_cfg)
    summary_path = result_dir / "process_pcaps_summary.json"

    pcaps = sorted(glob.glob(str(result_dir / "node_*.pcap")))
    if not pcaps:
        payload = {
            "result_dir": str(result_dir),
            "app": args.app,
            "status": "no_material",
            "total_pcaps": 0,
            "useful_pcaps": 0,
            "empty_pcaps": 0,
            "failed_pcaps": 0,
            "partial_failure": False,
            "warnings": [f"No pcaps found in {result_dir}"],
        }
        write_summary(summary_path, payload)
        print(f"[INFO] No pcaps found in {result_dir}")
        return 1

    out_csv = result_dir / "pcap_metrics.csv"
    tmp_csv = result_dir / "pcap_metrics.csv.tmp"
    out_jsonl = result_dir / "pcap_metrics.jsonl"
    tmp_jsonl = result_dir / "pcap_metrics.jsonl.tmp"

    useful_pcaps = 0
    empty_pcaps = 0
    failed_pcaps = 0
    warnings: List[str] = []

    with tmp_csv.open("w", newline="", encoding="utf-8") as fcsv, tmp_jsonl.open("w", encoding="utf-8") as fjsonl:
        writer = csv.writer(fcsv)
        writer.writerow(
            [
                "node",
                "frames",
                "bytes",
                "duration_sec",
                "pps",
                "bps",
                "min_len",
                "max_len",
                "mean_len",
                "tcpdump_captured",
                "tcpdump_filtered",
                "tcpdump_dropped",
                "pcap_file",
                "status",
                "tshark_rc",
                "tshark_error",
                "payload_type_frames_json",
                "payload_type_bytes_json",
                "protocol_type_frames_json",
                "protocol_type_bytes_json",
                "protocol_group_frames_json",
                "protocol_group_bytes_json",
            ]
        )

        for pcap in pcaps:
            pcap_path = Path(pcap)
            node = pcap_path.stem

            try:
                size = os.path.getsize(pcap)
            except OSError:
                size = 0

            netlog = result_dir / f"{node}.net.log"

            tcpdump_err = result_dir / f"{node}.tcpdump.stderr"
            if not tcpdump_err.exists():
                alt = result_dir / f"{node.split('_', 1)[1]}.tcpdump.stderr"
                if alt.exists():
                    tcpdump_err = alt
            tcpdump_stats = _parse_tcpdump_stderr(tcpdump_err)

            if size <= 0:
                empty_pcaps += 1
                writer.writerow(
                    [
                        node,
                        0,
                        0,
                        "0.000000",
                        "0.000000",
                        "0.000000",
                        "",
                        "",
                        "0.000000",
                        tcpdump_stats["captured"],
                        tcpdump_stats["filtered"],
                        tcpdump_stats["dropped"],
                        pcap,
                        "empty",
                        "",
                        "",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                    ]
                )
                fjsonl.write(
                    json.dumps(
                        {
                            "node": node,
                            "pcap_file": pcap,
                            "status": "empty",
                            "tcpdump": tcpdump_stats,
                            "app": args.app,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                continue

            stats, rc, err = compute_metrics_allow_partial(pcap, dfilt, payload_mode)

            truncated = False
            if rc != 0:
                lowered = err.lower()
                if "appears to have been cut short" in lowered or "cut short" in lowered:
                    truncated = True

            if rc == 0:
                status = "ok"
                useful_pcaps += 1
            elif stats.frames > 0 and truncated:
                status = "ok_truncated"
                useful_pcaps += 1
                warnings.append(f"Processed truncated pcap successfully enough for metrics: {pcap}")
            else:
                status = f"error:tshark_rc={rc}"
                failed_pcaps += 1

            payload_type_frames_json = json.dumps(stats.payload_type_frames, sort_keys=True)
            payload_type_bytes_json = json.dumps(stats.payload_type_bytes, sort_keys=True)
            protocol_type_frames_json = json.dumps(stats.protocol_type_frames, sort_keys=True)
            protocol_type_bytes_json = json.dumps(stats.protocol_type_bytes, sort_keys=True)
            protocol_group_frames_json = json.dumps(stats.protocol_group_frames, sort_keys=True)
            protocol_group_bytes_json = json.dumps(stats.protocol_group_bytes, sort_keys=True)

            writer.writerow(
                [
                    node,
                    stats.frames,
                    stats.bytes_total,
                    f"{stats.duration_sec:.6f}",
                    f"{stats.pps:.6f}",
                    f"{stats.bps:.6f}",
                    stats.min_len if stats.min_len is not None else "",
                    stats.max_len if stats.max_len is not None else "",
                    f"{stats.mean_len:.6f}",
                    tcpdump_stats["captured"],
                    tcpdump_stats["filtered"],
                    tcpdump_stats["dropped"],
                    pcap,
                    status,
                    rc,
                    err,
                    payload_type_frames_json,
                    payload_type_bytes_json,
                    protocol_type_frames_json,
                    protocol_type_bytes_json,
                    protocol_group_frames_json,
                    protocol_group_bytes_json,
                ]
            )

            detail = {
                "node": node,
                "app": args.app,
                "pcap_file": pcap,
                "status": status,
                "tshark_rc": rc,
                "tshark_error": err,
                "frames": stats.frames,
                "bytes_total": stats.bytes_total,
                "first_ts": stats.first_ts,
                "last_ts": stats.last_ts,
                "duration_sec": stats.duration_sec,
                "pps": stats.pps,
                "bps": stats.bps,
                "min_len": stats.min_len,
                "max_len": stats.max_len,
                "mean_len": stats.mean_len,
                "tcpdump": tcpdump_stats,
                "payload_type_frames": stats.payload_type_frames,
                "payload_type_bytes": stats.payload_type_bytes,
                "protocol_type_frames": stats.protocol_type_frames,
                "protocol_type_bytes": stats.protocol_type_bytes,
                "protocol_group_frames": stats.protocol_group_frames,
                "protocol_group_bytes": stats.protocol_group_bytes,
            }
            fjsonl.write(json.dumps(detail, sort_keys=True) + "\n")

            if args.append_netlog and netlog.exists():
                netlog_payload: Dict[str, object] = {
                    "PCAP_FRAMES": stats.frames,
                    "PCAP_BYTES": stats.bytes_total,
                    "PCAP_DURATION": stats.duration_sec,
                    "PCAP_PPS": stats.pps,
                    "PCAP_BPS": stats.bps,
                    "PCAP_MIN_LEN": stats.min_len if stats.min_len is not None else "",
                    "PCAP_MAX_LEN": stats.max_len if stats.max_len is not None else "",
                    "PCAP_MEAN_LEN": stats.mean_len,
                    "TCPDUMP_CAPTURED": tcpdump_stats["captured"],
                    "TCPDUMP_FILTERED": tcpdump_stats["filtered"],
                    "TCPDUMP_DROPPED": tcpdump_stats["dropped"],
                    "TSHARK_RC": rc,
                    "TSHARK_STATUS": status,
                }
                if payload_mode == "first_byte_hex":
                    for message_type, count in stats.payload_type_frames.items():
                        netlog_payload[f"PAYLOAD_MT_{message_type}_FRAMES"] = count
                    for message_type, value in stats.payload_type_bytes.items():
                        netlog_payload[f"PAYLOAD_MT_{message_type}_BYTES"] = value
                elif payload_mode == "batadv_packet_type":
                    for message_type, count in stats.protocol_type_frames.items():
                        netlog_payload[f"BATADV_{message_type}_FRAMES"] = count
                    for message_type, value in stats.protocol_type_bytes.items():
                        netlog_payload[f"BATADV_{message_type}_BYTES"] = value
                    for group, count in stats.protocol_group_frames.items():
                        netlog_payload[f"BATADV_GROUP_{group.upper()}_FRAMES"] = count
                    for group, value in stats.protocol_group_bytes.items():
                        netlog_payload[f"BATADV_GROUP_{group.upper()}_BYTES"] = value
                append_to_netlog(netlog, netlog_payload)

            if args.delete and status in ("ok", "ok_truncated", "empty"):
                try:
                    os.remove(pcap)
                except OSError:
                    pass

    tmp_csv.replace(out_csv)
    tmp_jsonl.replace(out_jsonl)

    if useful_pcaps > 0 and failed_pcaps == 0:
        summary_status = "success"
    elif useful_pcaps > 0:
        summary_status = "partial_success"
    else:
        summary_status = "failed"

    summary = {
        "result_dir": str(result_dir),
        "app": args.app,
        "status": summary_status,
        "total_pcaps": len(pcaps),
        "useful_pcaps": useful_pcaps,
        "empty_pcaps": empty_pcaps,
        "failed_pcaps": failed_pcaps,
        "partial_failure": useful_pcaps > 0 and failed_pcaps > 0,
        "csv_path": out_csv.name,
        "jsonl_path": out_jsonl.name,
        "warnings": warnings,
    }
    write_summary(summary_path, summary)

    print(f"[OK] Wrote {out_csv}")
    print(f"[OK] Wrote {out_jsonl}")
    print(f"[INFO] PCAP summary: useful={useful_pcaps} empty={empty_pcaps} failed={failed_pcaps}")

    return 0 if useful_pcaps > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
