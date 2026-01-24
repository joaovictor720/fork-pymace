#!/usr/bin/env python3
import argparse
import csv
import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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

    def __post_init__(self):
        self.payload_type_frames = {}
        self.payload_type_bytes = {}

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


def _tshark_fields(payload_mode: str) -> List[str]:
    fields = ["frame.time_epoch", "frame.len"]
    if payload_mode == "first_byte_hex":
        fields.append("data.data")
    return fields


def _run_tshark_rows(pcap_path: str, dfilt: str, fields: List[str]) -> subprocess.Popen:
    cmd = ["tshark", "-r", pcap_path, "-Y", dfilt, "-T", "fields", "-E", "separator=\t"]
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


def compute_metrics_allow_partial(pcap_path: str, dfilt: str, payload_mode: str) -> Tuple[PcapStats, int, str]:
    fields = _tshark_fields(payload_mode)
    proc = _run_tshark_rows(pcap_path, dfilt, fields)

    st = PcapStats()

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

        st.frames += 1
        st.bytes_total += ln

        if st.first_ts is None:
            st.first_ts = ts
        st.last_ts = ts

        if st.min_len is None or ln < st.min_len:
            st.min_len = ln
        if st.max_len is None or ln > st.max_len:
            st.max_len = ln

        if payload_mode == "first_byte_hex":
            data_data = parts[2].strip() if len(parts) > 2 else ""
            mt = _first_byte_from_hex(data_data)
            if mt is not None:
                st.payload_type_frames[mt] = st.payload_type_frames.get(mt, 0) + 1
                st.payload_type_bytes[mt] = st.payload_type_bytes.get(mt, 0) + ln

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

            st, rc, err = compute_metrics_allow_partial(pcap, dfilt, payload_mode)

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
                append_to_netlog(netlog, netlog_payload)

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
