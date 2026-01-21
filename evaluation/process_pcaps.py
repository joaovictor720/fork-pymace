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


def display_filter_for(algo: str, rapid_port: Optional[int]) -> str:
    if algo == "broadcast":
        return "eth.type==0x4305"
    if algo == "rapid":
        port = rapid_port if rapid_port is not None else 5001
        return f"udp.port=={port}"
    raise ValueError(f"Unknown algo: {algo}")


@dataclass
class PcapStats:
    frames: int = 0
    bytes_total: int = 0
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    min_len: Optional[int] = None
    max_len: Optional[int] = None

    eth_src_uniq: set = None  # type: ignore
    eth_dst_uniq: set = None  # type: ignore
    ip_src_uniq: set = None   # type: ignore
    ip_dst_uniq: set = None   # type: ignore

    udp_srcport_uniq: set = None  # type: ignore
    udp_dstport_uniq: set = None  # type: ignore

    rapid_type_frames: Dict[int, int] = None  # type: ignore
    rapid_type_bytes: Dict[int, int] = None   # type: ignore

    def __post_init__(self):
        self.eth_src_uniq = set()
        self.eth_dst_uniq = set()
        self.ip_src_uniq = set()
        self.ip_dst_uniq = set()
        self.udp_srcport_uniq = set()
        self.udp_dstport_uniq = set()
        self.rapid_type_frames = {}
        self.rapid_type_bytes = {}

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


def _tshark_fields_for(algo: str) -> List[str]:
    base = [
        "frame.time_epoch",
        "frame.len",
        "eth.src",
        "eth.dst",
        "ip.src",
        "ip.dst",
        "udp.srcport",
        "udp.dstport",
    ]
    if algo == "rapid":
        base.append("data.data")
    return base


def _run_tshark_rows(pcap_path: str, dfilt: str, fields: List[str]) -> subprocess.Popen:
    cmd = ["tshark", "-r", pcap_path, "-Y", dfilt, "-T", "fields", "-E", "separator=\t"]
    for f in fields:
        cmd += ["-e", f]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _rapid_msg_type_from_hex(data_data: str) -> Optional[int]:
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


def compute_metrics(pcap_path: str, algo: str, rapid_port: Optional[int]) -> PcapStats:
    dfilt = display_filter_for(algo, rapid_port)
    fields = _tshark_fields_for(algo)
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

        ts_s = parts[0].strip() if len(parts) > 0 else ""
        ln_s = parts[1].strip() if len(parts) > 1 else ""

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

        eth_src = parts[2].strip() if len(parts) > 2 else ""
        eth_dst = parts[3].strip() if len(parts) > 3 else ""
        ip_src = parts[4].strip() if len(parts) > 4 else ""
        ip_dst = parts[5].strip() if len(parts) > 5 else ""
        udp_sp = parts[6].strip() if len(parts) > 6 else ""
        udp_dp = parts[7].strip() if len(parts) > 7 else ""

        if eth_src:
            st.eth_src_uniq.add(eth_src)
        if eth_dst:
            st.eth_dst_uniq.add(eth_dst)
        if ip_src:
            st.ip_src_uniq.add(ip_src)
        if ip_dst:
            st.ip_dst_uniq.add(ip_dst)
        if udp_sp:
            st.udp_srcport_uniq.add(udp_sp)
        if udp_dp:
            st.udp_dstport_uniq.add(udp_dp)

        if algo == "rapid":
            data_data = parts[8].strip() if len(parts) > 8 else ""
            mt = _rapid_msg_type_from_hex(data_data)
            if mt is not None:
                st.rapid_type_frames[mt] = st.rapid_type_frames.get(mt, 0) + 1
                st.rapid_type_bytes[mt] = st.rapid_type_bytes.get(mt, 0) + ln

    assert proc.stderr is not None
    stderr = proc.stderr.read()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"tshark failed (rc={rc}): {stderr.strip()}")

    return st


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
    ap.add_argument("algo", choices=["broadcast", "rapid"])
    ap.add_argument("--delete", action="store_true", help="Delete pcap after successful processing")
    ap.add_argument("--append-netlog", action="store_true", help="Append PCAP_* fields to node_*.net.log")
    args = ap.parse_args()

    result_dir = Path(args.result_dir)
    if not result_dir.exists():
        raise SystemExit(f"[ERROR] result_dir not found: {result_dir}")

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
            "uniq_eth_src",
            "uniq_eth_dst",
            "uniq_ip_src",
            "uniq_ip_dst",
            "uniq_udp_srcport",
            "uniq_udp_dstport",
            "tcpdump_captured",
            "tcpdump_filtered",
            "tcpdump_dropped",
            "pcap_file",
            "status",
            "rapid_port",
            "rapid_type_frames_json",
            "rapid_type_bytes_json",
        ]
        w.writerow(cols)

        for pcap in pcaps:
            p = Path(pcap)
            node = p.stem  # node_5

            size = 0
            try:
                size = os.path.getsize(pcap)
            except OSError:
                size = 0

            netlog = result_dir / f"{node}.net.log"
            kv = _read_kv_file(netlog)
            rapid_port = None
            if args.algo == "rapid":
                try:
                    rapid_port = int(kv.get("RAPID_PORT", "5001"))
                except ValueError:
                    rapid_port = 5001

            tcpdump_err = result_dir / f"{node.split('_',1)[1]}.tcpdump.stderr"
            if not tcpdump_err.exists():
                tcpdump_err = result_dir / f"{node}.tcpdump.stderr"
            td = _parse_tcpdump_stderr(tcpdump_err)

            if size <= 0:
                row = [
                    node, 0, 0, "0.000000", "0.000000", "0.000000",
                    "", "", "0.000000",
                    0, 0, 0, 0, 0, 0,
                    td["captured"], td["filtered"], td["dropped"],
                    pcap, "empty",
                    rapid_port if rapid_port is not None else "",
                    "{}", "{}",
                ]
                w.writerow(row)
                fjsonl.write(json.dumps({
                    "node": node,
                    "pcap_file": pcap,
                    "status": "empty",
                    "tcpdump": td,
                    "algo": args.algo,
                    "rapid_port": rapid_port,
                }) + "\n")
                continue

            try:
                st = compute_metrics(pcap, args.algo, rapid_port)

                rapid_type_frames_json = json.dumps(st.rapid_type_frames, sort_keys=True)
                rapid_type_bytes_json = json.dumps(st.rapid_type_bytes, sort_keys=True)

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
                    len(st.eth_src_uniq),
                    len(st.eth_dst_uniq),
                    len(st.ip_src_uniq),
                    len(st.ip_dst_uniq),
                    len(st.udp_srcport_uniq),
                    len(st.udp_dstport_uniq),
                    td["captured"],
                    td["filtered"],
                    td["dropped"],
                    pcap,
                    "ok",
                    rapid_port if rapid_port is not None else "",
                    rapid_type_frames_json,
                    rapid_type_bytes_json,
                ]
                w.writerow(row)

                detail = {
                    "node": node,
                    "algo": args.algo,
                    "pcap_file": pcap,
                    "status": "ok",
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
                    "uniq_eth_src": sorted(st.eth_src_uniq),
                    "uniq_eth_dst": sorted(st.eth_dst_uniq),
                    "uniq_ip_src": sorted(st.ip_src_uniq),
                    "uniq_ip_dst": sorted(st.ip_dst_uniq),
                    "uniq_udp_srcport": sorted(st.udp_srcport_uniq),
                    "uniq_udp_dstport": sorted(st.udp_dstport_uniq),
                    "tcpdump": td,
                    "rapid_port": rapid_port,
                    "rapid_type_frames": st.rapid_type_frames,
                    "rapid_type_bytes": st.rapid_type_bytes,
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
                        "PCAP_UNIQ_ETH_SRC": len(st.eth_src_uniq),
                        "PCAP_UNIQ_ETH_DST": len(st.eth_dst_uniq),
                        "PCAP_UNIQ_IP_SRC": len(st.ip_src_uniq),
                        "PCAP_UNIQ_IP_DST": len(st.ip_dst_uniq),
                        "PCAP_UNIQ_UDP_SPORT": len(st.udp_srcport_uniq),
                        "PCAP_UNIQ_UDP_DPORT": len(st.udp_dstport_uniq),
                        "TCPDUMP_CAPTURED": td["captured"],
                        "TCPDUMP_FILTERED": td["filtered"],
                        "TCPDUMP_DROPPED": td["dropped"],
                    }
                    if args.algo == "rapid":
                        for mt, cnt in st.rapid_type_frames.items():
                            netlog_payload[f"RAPID_MT_{mt}_FRAMES"] = cnt
                        for mt, b in st.rapid_type_bytes.items():
                            netlog_payload[f"RAPID_MT_{mt}_BYTES"] = b
                    append_to_netlog(netlog, netlog_payload)

                if args.delete:
                    os.remove(pcap)

            except Exception as e:
                row = [
                    node, "", "", "", "", "", "", "", "",
                    "", "", "", "", "", "",
                    td["captured"], td["filtered"], td["dropped"],
                    pcap, f"error:{e}",
                    rapid_port if rapid_port is not None else "",
                    "{}", "{}",
                ]
                w.writerow(row)
                fjsonl.write(json.dumps({
                    "node": node,
                    "pcap_file": pcap,
                    "status": "error",
                    "error": str(e),
                    "tcpdump": td,
                    "algo": args.algo,
                    "rapid_port": rapid_port,
                }) + "\n")

    tmp_csv.replace(out_csv)
    tmp_jsonl.replace(out_jsonl)
    print(f"[OK] Wrote {out_csv}")
    print(f"[OK] Wrote {out_jsonl}")


if __name__ == "__main__":
    main()
