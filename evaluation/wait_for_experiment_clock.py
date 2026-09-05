#!/usr/bin/env python3
"""Wait until the synchronized coverage epoch published by MACE."""

import argparse
import json
import time
from pathlib import Path


def read_clock(path: Path, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            with path.open(encoding="utf-8") as stream:
                clock = json.load(stream)
            if clock.get("schema") != "mace_experiment_clock_v1":
                raise ValueError("unsupported experiment clock schema")
            return clock
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as error:
            last_error = error
            time.sleep(0.02)
    raise TimeoutError(
        "experiment clock was not available at {}: {}".format(path, last_error)
    )


def wait_until_unix(timestamp_s: float) -> None:
    while True:
        remaining = float(timestamp_s) - time.time()
        if remaining <= 0.0:
            return
        time.sleep(min(remaining, 0.05))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clock", required=True, type=Path)
    parser.add_argument("--field", default="coverage_start_unix_s")
    parser.add_argument("--file-timeout", type=float, default=30.0)
    args = parser.parse_args()

    if args.file_timeout <= 0.0:
        parser.error("--file-timeout must be positive")
    clock = read_clock(args.clock, args.file_timeout)
    if args.field not in clock:
        raise SystemExit("missing experiment clock field: " + args.field)
    wait_until_unix(float(clock[args.field]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
