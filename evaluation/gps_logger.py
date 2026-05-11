#!/usr/bin/env python3
import argparse
import csv
import pickle
import socket
import struct
import sys
import time
from typing import Any, Tuple


TIMEOUT_S = 3.0
RETRIES = 3
RETRY_SLEEP_S = 0.05
FLUSH_EVERY = 10


class GPSBridge:
    def __init__(
        self,
        tag: str,
        timeout_s: float = TIMEOUT_S,
        retries: int = RETRIES,
        retry_sleep_s: float = RETRY_SLEEP_S,
    ) -> None:
        self.tag = tag
        self.sock_path = "/tmp/" + self.tag + "_gps.sock"
        self.timeout_s = timeout_s
        self.retries = max(1, retries)
        self.retry_sleep_s = retry_sleep_s

    def _recv_exact(self, sock: socket.socket, n: int):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _try_once(self):
        data = [self.tag, "GET_POSITION"]
        payload = pickle.dumps(data)
        length = len(payload)

        gps = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        gps.settimeout(self.timeout_s)

        try:
            gps.connect(self.sock_path)
            gps.sendall(struct.pack("!I", length))
            gps.sendall(payload)

            lengthbuf = self._recv_exact(gps, 4)
            if lengthbuf is None:
                return None

            resp_len, = struct.unpack("!I", lengthbuf)
            buf = self._recv_exact(gps, int(resp_len))
            return buf
        except Exception:
            return None
        finally:
            try:
                gps.close()
            except Exception:
                pass

    def get_position(self) -> bytes:
        for i in range(self.retries):
            out = self._try_once()
            if out is not None:
                return out
            if i + 1 < self.retries:
                time.sleep(self.retry_sleep_s)
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


def poll_once(
    tag: str,
    timeout_s: float = TIMEOUT_S,
    retries: int = RETRIES,
    retry_sleep_s: float = RETRY_SLEEP_S,
) -> Tuple[float, float, float, int]:
    b = GPSBridge(
        tag,
        timeout_s=timeout_s,
        retries=retries,
        retry_sleep_s=retry_sleep_s,
    )
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
    ap.add_argument("--timeout", default=TIMEOUT_S, type=float)
    ap.add_argument("--retries", default=RETRIES, type=int)
    ap.add_argument("--retry-sleep", default=RETRY_SLEEP_S, type=float)
    ap.add_argument(
        "--drop-invalid",
        action="store_true",
        help="Do not write ok=0 rows to the CSV.",
    )
    ap.add_argument(
        "--stop-after-invalid",
        default=0,
        type=int,
        help=(
            "After at least one valid sample, stop when this many consecutive "
            "invalid polls occur. 0 disables this guard."
        ),
    )
    args = ap.parse_args()

    t0 = time.monotonic()
    rows_written = 0
    valid_rows = 0
    invalid_polls = 0
    consecutive_invalid = 0
    saw_valid = False

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "node", "x_m", "y_m", "z_m", "ok"])

        next_t = t0
        nrows = 0
        while True:
            now = time.monotonic()
            elapsed = now - t0
            if elapsed > args.duration:
                break

            x, y, z, ok = poll_once(
                args.tag,
                timeout_s=args.timeout,
                retries=args.retries,
                retry_sleep_s=args.retry_sleep,
            )

            if ok:
                saw_valid = True
                consecutive_invalid = 0
                valid_rows += 1
            else:
                invalid_polls += 1
                if saw_valid:
                    consecutive_invalid += 1
                    if (
                        args.stop_after_invalid > 0
                        and consecutive_invalid >= args.stop_after_invalid
                    ):
                        break

            if ok or not args.drop_invalid:
                w.writerow([f"{elapsed:.9f}", args.node, f"{x:.6f}", f"{y:.6f}", f"{z:.6f}", ok])
                rows_written += 1
                nrows += 1
                if (nrows % FLUSH_EVERY) == 0:
                    f.flush()

            next_t += args.interval
            sleep_s = next_t - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

        f.flush()

    print(
        "gps_logger summary: "
        f"tag={args.tag} node={args.node} rows={rows_written} "
        f"valid={valid_rows} invalid_polls={invalid_polls}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
