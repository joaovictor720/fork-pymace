"""Read-only acceptance checks for the small, two-node portability experiments."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--wifi", action="store_true")
    args = parser.parse_args()
    root = args.run_dir
    if args.wifi:
        info = json.loads((root / "batman_module.json").read_text())
        if not info["mode_matches_request"] or info["effective_mode"] != "emulated_wifi":
            raise SystemExit("Incorrect module mode")
    for i in range(2):
        log = (root / f"node_{i}.net.log").read_text()
        if re.findall(r"^APP_RC=(\d+)$", log, re.M) != ["0"]:
            raise SystemExit(f"node {i}: application did not finish successfully")
        capture = root / f"node_{i}.pcap"
        command = ["tshark", "-r", str(capture), "-Y", "udp.port == 5001",
                   "-T", "fields", "-e", "frame.number"]
        rows = subprocess.check_output(command, text=True).splitlines()
        if not rows:
            raise SystemExit(f"node {i}: no application UDP traffic captured")
        if args.wifi:
            rows = subprocess.check_output([
                "tshark", "-r", str(capture), "-Y", "batadv.bcast.seq && udp.port == 5001",
                "-T", "fields", "-e", "eth.src", "-e", "batadv.bcast.orig", "-e", "batadv.bcast.seq",
            ], text=True).splitlines()
            if 3 not in Counter(rows).values():
                raise SystemExit(f"node {i}: no broadcast sequence captured three times")
        gps = (root / f"node_{i}.gps.csv").read_text().splitlines()
        if len(gps) < 2:
            raise SystemExit(f"node {i}: missing GPS samples")
    print("[OK] Both applications exited successfully; packet and GPS captures exist" +
          ("; repeated BATADV_BCAST verified" if args.wifi else ""))


if __name__ == "__main__":
    main()
