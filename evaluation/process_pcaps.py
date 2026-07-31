#!/usr/bin/env python3
import argparse
import csv
import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def _read_kv_file(path: Path) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    if not path.exists():
        return kv
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()
    return kv


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


def classification_config_for_app(app: str, apps_cfg: Dict[str, object]) -> Dict[str, object]:
    apps = apps_cfg.get("apps", {})
    app_cfg = apps.get(app) if isinstance(apps, dict) else None
    if not isinstance(app_cfg, dict):
        raise ValueError(f"App not found or invalid in apps.json: {app}")
    raw = app_cfg.get("frame_classification")
    if raw is None:
        return {"mode": "none", "unclassified_sample_limit": 5}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid frame_classification for app={app}")

    cfg = dict(raw)
    mode = str(cfg.get("mode", "none")).strip().lower()
    if mode not in ("none", "all_payload", "protocol_type_byte", "batadv"):
        raise ValueError(f"Unsupported frame_classification.mode={mode!r} for app={app}")
    cfg["mode"] = mode

    if mode == "protocol_type_byte":
        cfg["payload_types"] = _config_ints(cfg, "payload_types", app)
        cfg["control_types"] = _config_ints(cfg, "control_types", app)
        if cfg["payload_types"] & cfg["control_types"]:  # type: ignore[operator]
            raise ValueError(f"Overlapping payload/control types for app={app}")

    if mode == "batadv":
        cfg["application_udp_ports"] = _config_ints(cfg, "application_udp_ports", app)
        if not cfg["application_udp_ports"]:
            raise ValueError(f"No application_udp_ports configured for app={app}")

    cfg["unclassified_sample_limit"] = max(0, int(cfg.get("unclassified_sample_limit", 5)))
    return cfg


def _config_ints(cfg: Dict[str, object], key: str, app: str) -> Set[int]:
    value = cfg.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Invalid frame_classification.{key} for app={app}")
    return {int(item) for item in value}


@dataclass
class PcapStats:
    frames: int = 0
    bytes_total: int = 0
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    min_len: Optional[int] = None
    max_len: Optional[int] = None

    payload_type_frames: Dict[int, int] = field(default_factory=dict)
    payload_type_bytes: Dict[int, int] = field(default_factory=dict)

    payload_frames: int = 0
    control_frames: int = 0
    unclassified_frames: int = 0
    classification_enabled: bool = False

    classification_type_frames: Dict[str, Dict[str, int]] = field(default_factory=dict)
    unclassified_reasons: Dict[str, int] = field(default_factory=dict)
    unclassified_sample_frames: Dict[str, List[int]] = field(default_factory=dict)

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
        d = self.duration_sec
        if d <= 0.0:
            return 0.0
        return self.frames / d

    @property
    def bps(self) -> float:
        d = self.duration_sec
        if d <= 0.0:
            return 0.0
        return (self.bytes_total * 8.0) / d

    @property
    def classification_residual(self) -> int:
        return self.frames - (
            self.payload_frames + self.control_frames + self.unclassified_frames
        )

    @property
    def classification_status(self) -> str:
        if not self.classification_enabled:
            return "unavailable"
        if self.classification_residual != 0:
            return "error_classification_residual"
        if self.unclassified_frames > 0:
            return "warning_unclassified"
        return "ok"

    def record_classification(
        self,
        category: str,
        type_name: str,
        frame_number: Optional[int],
        reason: Optional[str],
        sample_limit: int,
    ) -> None:
        if category == "payload":
            self.payload_frames += 1
        elif category == "control":
            self.control_frames += 1
        elif category == "unclassified":
            self.unclassified_frames += 1
        else:
            # A classifier bug must be visible in classification_residual.
            return

        by_category_frames = self.classification_type_frames.setdefault(category, {})
        by_category_frames[type_name] = by_category_frames.get(type_name, 0) + 1

        if category != "unclassified":
            return
        reason = reason or "unspecified"
        self.unclassified_reasons[reason] = self.unclassified_reasons.get(reason, 0) + 1
        samples = self.unclassified_sample_frames.setdefault(reason, [])
        if frame_number is not None and len(samples) < sample_limit:
            samples.append(frame_number)


BATADV_TYPE_NAMES = {
    0x00: "BATADV_IV_OGM",
    0x01: "BATADV_BCAST",
    0x02: "BATADV_CODED",
    0x03: "BATADV_ELP",
    0x04: "BATADV_OGM2",
    # BATADV_MCAST was introduced after the kernel version used by the
    # original experiments, but recognizing it keeps diagnostics useful when
    # processing captures produced by newer batman-adv versions.
    0x05: "BATADV_MCAST",
    0x40: "BATADV_UNICAST",
    0x41: "BATADV_UNICAST_FRAG",
    0x42: "BATADV_UNICAST_4ADDR",
    0x43: "BATADV_ICMP",
    0x44: "BATADV_UNICAST_TVLV",
}

BATADV_CONTROL_TYPES = {0x00, 0x03, 0x04, 0x43, 0x44}
BATADV_DATA_TYPES = {0x01, 0x02, 0x05, 0x40, 0x41}
BATADV_UNICAST_4ADDR = 0x42
BATADV_4ADDR_DATA_SUBTYPE = 0x01
BATADV_4ADDR_CONTROL_SUBTYPES = {
    0x02: "BATADV_P_DAT_DHT_GET",
    0x03: "BATADV_P_DAT_DHT_PUT",
    0x04: "BATADV_P_DAT_CACHE_REPLY",
}

DHCP_UDP_PORTS = {67, 68, 546, 547}


def _tshark_fields(payload_mode: str, classification_mode: str = "none") -> List[str]:
    # frame.number is only diagnostic. frame.time_epoch/frame.len and the
    # display filter remain the exact source of the legacy totals.
    fields = ["frame.time_epoch", "frame.len", "frame.number"]
    if payload_mode == "first_byte_hex":
        fields.append("data.data")
    if classification_mode == "batadv":
        fields.extend(
            [
                "batadv.batman.packet_type",
                "batadv.unicast_4addr.subtype",
                "udp.srcport",
                "udp.dstport",
                "arp.opcode",
                "icmp.type",
                "icmpv6.type",
                "dhcp.type",
            ]
        )
    return fields


def _run_tshark_rows(pcap_path: str, dfilt: str, fields: List[str]) -> subprocess.Popen:
    cmd = ["tshark"]
    if "data.data" in fields:
        # UDP/5001 is registered as CPFI in Wireshark. The experiment protocols
        # are not CPFI; disabling that dissector exposes their bytes as data.data.
        cmd.extend(["--disable-protocol", "cpfi"])
    if "batadv.batman.packet_type" in fields:
        # Explicit decode-as avoids depending on the local Wireshark profile's
        # ethertype association. It does not broaden the display filter.
        cmd.extend(["-d", "ethertype==0x4305,batadv"])
    cmd.extend([
        "-r", pcap_path,
        "-Y",
        dfilt,
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
        "-E",
        "aggregator=,",
    ])
    for f in fields:
        cmd += ["-e", f]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _first_byte_from_hex(data_data: str) -> Optional[int]:
    if not data_data:
        return None
    s = data_data.strip()
    if not s:
        return None
    if ":" in s:
        s = s.replace(":", "")
    if len(s) < 2:
        return None
    try:
        return int(s[0:2], 16)
    except ValueError:
        return None


def _int_values(value: str) -> Set[int]:
    values: Set[int] = set()
    if not value:
        return values
    for part in re.split(r"[,; ]+", value.strip()):
        if not part:
            continue
        try:
            values.add(int(part, 0))
        except ValueError:
            continue
    return values


def _first_int_field(value: str) -> Optional[int]:
    values = _int_values(value)
    if not values:
        return None
    return sorted(values)[0]


def _protocol_type_name(message_type: Optional[int], cfg: Dict[str, object]) -> str:
    if message_type is None:
        return "MISSING_PROTOCOL_TYPE"
    names = cfg.get("type_names", {})
    if isinstance(names, dict):
        name = names.get(str(message_type))
        if name is not None:
            return str(name)
    return f"PROTOCOL_TYPE_0x{message_type:02x}"


def _classify_protocol_type(
    message_type: Optional[int], cfg: Dict[str, object]
) -> Tuple[str, str, Optional[str]]:
    type_name = _protocol_type_name(message_type, cfg)
    if message_type is None:
        return "unclassified", type_name, "missing_protocol_type"

    payload_types = cfg.get("payload_types", set())
    control_types = cfg.get("control_types", set())
    if isinstance(payload_types, set) and message_type in payload_types:
        return "payload", type_name, None
    if isinstance(control_types, set) and message_type in control_types:
        return "control", type_name, None
    return (
        "unclassified",
        type_name,
        f"unknown_protocol_type:0x{message_type:02x}",
    )


def _batadv_type_name(packet_type: Optional[int], subtype: Optional[int]) -> str:
    if packet_type is None:
        return "BATADV_MISSING_PACKET_TYPE"
    base = BATADV_TYPE_NAMES.get(packet_type, f"BATADV_UNKNOWN_0x{packet_type:02x}")
    if packet_type != BATADV_UNICAST_4ADDR:
        return base
    if subtype == BATADV_4ADDR_DATA_SUBTYPE:
        return f"{base}_DATA"
    if subtype in BATADV_4ADDR_CONTROL_SUBTYPES:
        return BATADV_4ADDR_CONTROL_SUBTYPES[subtype]
    if subtype is None:
        return f"{base}_MISSING_SUBTYPE"
    return f"{base}_UNKNOWN_SUBTYPE_0x{subtype:02x}"


def _network_control_type(fields: Dict[str, str], udp_ports: Set[int]) -> Optional[str]:
    if fields.get("arp.opcode", "").strip():
        return "ARP"
    if fields.get("icmp.type", "").strip():
        return "ICMP"
    if fields.get("icmpv6.type", "").strip():
        return "ICMPV6"
    if fields.get("dhcp.type", "").strip() or udp_ports & DHCP_UDP_PORTS:
        return "DHCP"
    return None


def _classify_batadv(
    fields: Dict[str, str], cfg: Dict[str, object]
) -> Tuple[str, str, Optional[str]]:
    packet_type = _first_int_field(fields.get("batadv.batman.packet_type", ""))
    subtype = _first_int_field(fields.get("batadv.unicast_4addr.subtype", ""))
    type_name = _batadv_type_name(packet_type, subtype)

    if packet_type is None:
        return "unclassified", type_name, "batadv_missing_packet_type"
    if packet_type not in BATADV_TYPE_NAMES:
        return (
            "unclassified",
            type_name,
            f"batadv_unknown_packet_type:0x{packet_type:02x}",
        )
    if packet_type in BATADV_CONTROL_TYPES:
        return "control", type_name, None

    if packet_type == BATADV_UNICAST_4ADDR:
        if subtype in BATADV_4ADDR_CONTROL_SUBTYPES:
            return "control", type_name, None
        if subtype != BATADV_4ADDR_DATA_SUBTYPE:
            if subtype is None:
                reason = "batadv_unicast_4addr_missing_subtype"
            else:
                reason = f"batadv_unicast_4addr_unknown_subtype:0x{subtype:02x}"
            return "unclassified", type_name, reason

    if packet_type not in BATADV_DATA_TYPES and packet_type != BATADV_UNICAST_4ADDR:
        return "unclassified", type_name, f"batadv_unmapped_type:{type_name}"

    udp_ports = _int_values(fields.get("udp.srcport", ""))
    udp_ports.update(_int_values(fields.get("udp.dstport", "")))
    application_ports = cfg.get("application_udp_ports", set())
    if isinstance(application_ports, set) and udp_ports & application_ports:
        return "payload", f"{type_name}_APPLICATION_UDP", None

    network_control = _network_control_type(fields, udp_ports)
    if network_control is not None:
        return "control", f"{type_name}_{network_control}", None

    port_suffix = ""
    if udp_ports:
        port_suffix = ":" + ",".join(str(port) for port in sorted(udp_ports))
    return (
        "unclassified",
        type_name,
        f"batadv_transport_without_application_signature:{type_name}{port_suffix}",
    )


def _classify_row(
    fields: Dict[str, str],
    payload_mode: str,
    cfg: Dict[str, object],
) -> Tuple[str, str, Optional[str], Optional[int]]:
    mode = cfg.get("mode", "none")
    message_type: Optional[int] = None

    if payload_mode == "first_byte_hex":
        message_type = _first_byte_from_hex(fields.get("data.data", ""))

    if mode == "all_payload":
        return "payload", str(cfg.get("type_name", "APPLICATION_PAYLOAD")), None, message_type
    if mode == "protocol_type_byte":
        category, type_name, reason = _classify_protocol_type(message_type, cfg)
        return category, type_name, reason, message_type
    if mode == "batadv":
        category, type_name, reason = _classify_batadv(fields, cfg)
        return category, type_name, reason, message_type
    return "", "CLASSIFICATION_UNAVAILABLE", "classification_unavailable", message_type


def compute_metrics_allow_partial(
    pcap_path: str,
    dfilt: str,
    payload_mode: str,
    classification_cfg: Optional[Dict[str, object]] = None,
) -> Tuple[PcapStats, int, str]:
    if classification_cfg is None:
        classification_cfg = {"mode": "none", "unclassified_sample_limit": 5}
    classification_mode = str(classification_cfg.get("mode", "none"))
    fields = _tshark_fields(payload_mode, classification_mode)
    proc = _run_tshark_rows(pcap_path, dfilt, fields)

    st = PcapStats()
    st.classification_enabled = classification_mode != "none"
    sample_limit = int(classification_cfg.get("unclassified_sample_limit", 5))

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue

        row_fields = {
            field_name: (parts[index].strip() if index < len(parts) else "")
            for index, field_name in enumerate(fields)
        }

        ts_s = row_fields["frame.time_epoch"]
        ln_s = row_fields["frame.len"]

        try:
            ts = float(ts_s)
            ln = int(ln_s)
        except ValueError:
            continue

        st.frames += 1
        st.bytes_total += ln

        if st.first_ts is None:
            st.first_ts = ts
        st.last_ts = ts

        if st.min_len is None or ln < st.min_len:
            st.min_len = ln
        if st.max_len is None or ln > st.max_len:
            st.max_len = ln

        frame_number: Optional[int]
        try:
            frame_number = int(row_fields.get("frame.number", ""))
        except ValueError:
            frame_number = None

        category, type_name, reason, message_type = _classify_row(
            row_fields, payload_mode, classification_cfg
        )

        if message_type is not None:
            st.payload_type_frames[message_type] = st.payload_type_frames.get(message_type, 0) + 1
            st.payload_type_bytes[message_type] = st.payload_type_bytes.get(message_type, 0) + ln

        if st.classification_enabled:
            st.record_classification(
                category,
                type_name,
                frame_number,
                reason,
                sample_limit,
            )

    assert proc.stderr is not None
    stderr = proc.stderr.read()
    rc = proc.wait()
    return st, rc, stderr.strip()


def append_to_netlog(netlog_path: Path, payload: Dict[str, object]) -> None:
    with netlog_path.open("a", encoding="utf-8") as f:
        for k, v in payload.items():
            if isinstance(v, float):
                f.write(f"{k}={v:.6f}\n")
            else:
                f.write(f"{k}={v}\n")


def main() -> None:
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
    classification_cfg = classification_config_for_app(args.app, apps_cfg)

    pcaps = sorted(glob.glob(str(result_dir / "node_*.pcap")))
    if not pcaps:
        print(f"[INFO] No pcaps found in {result_dir}")
        return

    out_csv = result_dir / "pcap_metrics.csv"
    tmp_csv = result_dir / "pcap_metrics.csv.tmp"
    out_jsonl = result_dir / "pcap_metrics.jsonl"
    tmp_jsonl = result_dir / "pcap_metrics.jsonl.tmp"

    with tmp_csv.open("w", newline="", encoding="utf-8") as fcsv, tmp_jsonl.open("w", encoding="utf-8") as fjsonl:
        w = csv.writer(fcsv)

        cols = [
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
            "payload_frames",
            "control_frames",
            "unclassified_frames",
            "classification_residual",
            "classification_status",
            "classification_type_frames_json",
            "unclassified_reasons_json",
            "unclassified_sample_frames_json",
        ]
        w.writerow(cols)

        for pcap in pcaps:
            p = Path(pcap)
            node = p.stem

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
            td = _parse_tcpdump_stderr(tcpdump_err)

            if size <= 0:
                row = [
                    node, 0, 0, "0.000000", "0.000000", "0.000000",
                    "", "", "0.000000",
                    td["captured"], td["filtered"], td["dropped"],
                    pcap, "empty",
                    "", "",
                    "{}", "{}",
                    "", "", "", "",
                    "unavailable",
                    "{}", "{}", "{}",
                ]
                w.writerow(row)
                fjsonl.write(json.dumps({
                    "node": node,
                    "pcap_file": pcap,
                    "status": "empty",
                    "tcpdump": td,
                    "app": args.app,
                }) + "\n")
                continue

            st, rc, err = compute_metrics_allow_partial(
                pcap, dfilt, payload_mode, classification_cfg
            )

            truncated = False
            if rc != 0:
                e = err.lower()
                if "appears to have been cut short" in e or "cut short" in e:
                    truncated = True

            if rc == 0:
                status = "ok"
            else:
                if st.frames > 0 and truncated:
                    status = "ok_truncated"
                else:
                    status = f"error:tshark_rc={rc}"

            payload_type_frames_json = json.dumps(st.payload_type_frames, sort_keys=True)
            payload_type_bytes_json = json.dumps(st.payload_type_bytes, sort_keys=True)
            classification_usable = (
                status in ("ok", "ok_truncated") and st.classification_enabled
            )
            if classification_usable:
                payload_frames: object = st.payload_frames
                control_frames: object = st.control_frames
                unclassified_frames: object = st.unclassified_frames
                classification_residual: object = st.classification_residual
                classification_status = st.classification_status
            else:
                payload_frames = ""
                control_frames = ""
                unclassified_frames = ""
                classification_residual = ""
                classification_status = "unavailable"

            classification_type_frames_json = json.dumps(
                st.classification_type_frames if classification_usable else {}, sort_keys=True
            )
            unclassified_reasons_json = json.dumps(
                st.unclassified_reasons if classification_usable else {}, sort_keys=True
            )
            unclassified_sample_frames_json = json.dumps(
                st.unclassified_sample_frames if classification_usable else {}, sort_keys=True
            )

            row = [
                node,
                st.frames,
                st.bytes_total,
                f"{st.duration_sec:.6f}",
                f"{st.pps:.6f}",
                f"{st.bps:.6f}",
                st.min_len if st.min_len is not None else "",
                st.max_len if st.max_len is not None else "",
                f"{st.mean_len:.6f}",
                td["captured"],
                td["filtered"],
                td["dropped"],
                pcap,
                status,
                rc,
                err,
                payload_type_frames_json,
                payload_type_bytes_json,
                payload_frames,
                control_frames,
                unclassified_frames,
                classification_residual,
                classification_status,
                classification_type_frames_json,
                unclassified_reasons_json,
                unclassified_sample_frames_json,
            ]
            w.writerow(row)

            detail = {
                "node": node,
                "app": args.app,
                "pcap_file": pcap,
                "status": status,
                "tshark_rc": rc,
                "tshark_error": err,
                "frames": st.frames,
                "bytes_total": st.bytes_total,
                "first_ts": st.first_ts,
                "last_ts": st.last_ts,
                "duration_sec": st.duration_sec,
                "pps": st.pps,
                "bps": st.bps,
                "min_len": st.min_len,
                "max_len": st.max_len,
                "mean_len": st.mean_len,
                "tcpdump": td,
                "payload_type_frames": st.payload_type_frames,
                "payload_type_bytes": st.payload_type_bytes,
                "payload_frames": payload_frames if classification_usable else None,
                "control_frames": control_frames if classification_usable else None,
                "unclassified_frames": unclassified_frames if classification_usable else None,
                "classification_residual": (
                    classification_residual if classification_usable else None
                ),
                "classification_status": classification_status,
                "classification_type_frames": (
                    st.classification_type_frames if classification_usable else {}
                ),
                "unclassified_reasons": (
                    st.unclassified_reasons if classification_usable else {}
                ),
                "unclassified_sample_frames": (
                    st.unclassified_sample_frames if classification_usable else {}
                ),
            }
            fjsonl.write(json.dumps(detail, sort_keys=True) + "\n")

            if args.append_netlog and netlog.exists():
                netlog_payload: Dict[str, object] = {
                    "PCAP_FRAMES": st.frames,
                    "PCAP_BYTES": st.bytes_total,
                    "PCAP_DURATION": st.duration_sec,
                    "PCAP_PPS": st.pps,
                    "PCAP_BPS": st.bps,
                    "PCAP_MIN_LEN": st.min_len if st.min_len is not None else "",
                    "PCAP_MAX_LEN": st.max_len if st.max_len is not None else "",
                    "PCAP_MEAN_LEN": st.mean_len,
                    "TCPDUMP_CAPTURED": td["captured"],
                    "TCPDUMP_FILTERED": td["filtered"],
                    "TCPDUMP_DROPPED": td["dropped"],
                    "TSHARK_RC": rc,
                    "TSHARK_STATUS": status,
                }
                if payload_mode == "first_byte_hex":
                    for mt, cnt in st.payload_type_frames.items():
                        netlog_payload[f"PAYLOAD_MT_{mt}_FRAMES"] = cnt
                    for mt, b in st.payload_type_bytes.items():
                        netlog_payload[f"PAYLOAD_MT_{mt}_BYTES"] = b
                if classification_usable:
                    netlog_payload.update(
                        {
                            "PCAP_PAYLOAD_FRAMES": st.payload_frames,
                            "PCAP_CONTROL_FRAMES": st.control_frames,
                            "PCAP_UNCLASSIFIED_FRAMES": st.unclassified_frames,
                            "PCAP_CLASSIFICATION_RESIDUAL": st.classification_residual,
                            "PCAP_CLASSIFICATION_STATUS": st.classification_status,
                        }
                    )
                append_to_netlog(netlog, netlog_payload)

            if classification_usable and st.classification_status != "ok":
                print(
                    f"[WARN] {node}: classification_status={st.classification_status} "
                    f"unclassified={st.unclassified_frames} "
                    f"residual={st.classification_residual} "
                    f"reasons={json.dumps(st.unclassified_reasons, sort_keys=True)}"
                )

            if args.delete and status in ("ok", "ok_truncated", "empty"):
                try:
                    os.remove(pcap)
                except OSError:
                    pass

    tmp_csv.replace(out_csv)
    tmp_jsonl.replace(out_jsonl)
    print(f"[OK] Wrote {out_csv}")
    print(f"[OK] Wrote {out_jsonl}")


if __name__ == "__main__":
    main()
