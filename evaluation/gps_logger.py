#!/usr/bin/env python3
import argparse
import csv
import pickle
import socket
import struct
import time
import traceback
from typing import Any, Optional, Tuple


class GPSBridge:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def get_position(self) -> bytes:
        data = [self.tag, "GET_POSITION"]
        payload = pickle.dumps(data)
        length = len(payload)

        gps = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        gps.settimeout(1)

        sock_path = "/tmp/" + self.tag + "_gps.sock"

        try:
            gps.connect(sock_path)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError):
            gps.close()
            return pickle.dumps([-1.0, -1.0, -1.0])

        try:
            gps.sendall(struct.pack("!I", length))
            gps.sendall(payload)

            lengthbuf = gps.recv(4)
            if lengthbuf is None or len(lengthbuf) != 4:
                gps.close()
                return pickle.dumps([-1.0, -1.0, -1.0])

            resp_len, = struct.unpack("!I", lengthbuf)
            buf = b""
            remaining = resp_len
            while remaining > 0:
                chunk = gps.recv(remaining)
                if not chunk:
                    gps.close()
                    return pickle.dumps([-1.0, -1.0, -1.0])
                buf += chunk
                remaining -= len(chunk)

            gps.close()
            return buf
        except Exception:
            gps.close()
            return pickle.dumps([-1.0, -1.0, -1.0])


def _coerce_xyz(obj: Any) -> Tuple[float, float, float, int]:
    try:
        if isinstance(obj, dict):
            x = float(obj.get("x", -1.0))
            y = float(obj.get("y", -1.0))
            z = float(obj.get("z", 0.0))
        elif isinstance(obj, (list, tuple)):
            if len(obj) == 2:
                x = float(obj[0])
                y = float(obj[1])
                z = 0.0
            elif len(obj) >= 3:
                x = float(obj[0])
                y = float(obj[1])
                z = float(obj[2])
            else:
                return -1.0, -1.0, -1.0, 0
        else:
            return -1.0, -1.0, -1.0, 0

        ok = 1
        if x == -1.0 and y == -1.0 and z == -1.0:
            ok = 0
        return x, y, z, ok
    except Exception:
        return -1.0, -1.0, -1.0, 0


def poll_once(tag: str) -> Tuple[float, float, float, int]:
    b = GPSBridge(tag)
    raw = b.get_position()
    try:
        obj = pickle.loads(raw)
    except Exception:
        return -1.0, -1.0, -1.0, 0
    return _coerce_xyz(obj)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--node", required=True, type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", required=True, type=float)
    ap.add_argument("--duration", required=True, type=float)
    args = ap.parse_args()

    t0 = time.monotonic()

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "node", "x_m", "y_m", "z_m", "ok"])

        next_t = t0
        while True:
            now = time.monotonic()
            elapsed = now - t0
            if elapsed > args.duration:
                break

            x, y, z, ok = poll_once(args.tag)
            w.writerow([f"{elapsed:.9f}", args.node, f"{x:.6f}", f"{y:.6f}", f"{z:.6f}", ok])
            f.flush()

            next_t += args.interval
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
