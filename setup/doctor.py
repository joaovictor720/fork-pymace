#!/usr/bin/env python3
"""Read-only host preflight: never sudo, load modules, or create network devices."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def diagnose(mode):
    checks = []

    def check(name, ok, detail):
        checks.append(dict(name=name, ok=bool(ok), detail=str(detail)))

    for name in ("ip", "tc", "nft", "ethtool", "vnoded", "vcmd", "tcpdump", "tshark", "g++"):
        path = shutil.which(name)
        check(name, path, path or "missing; setup/setup.sh installs system dependencies")
    code, out = command(sys.executable, "-c", "import pymace; from core import constants; print(constants.COREDPY_VERSION)")
    check("Python/CORE/MACE import", code == 0, out)
    code, out = command("g++", "-std=c++17", "-x", "c++", "-fsyntax-only", "-include", "nlohmann/json.hpp", "/dev/null")
    check("C++17/nlohmann-json", code == 0, out or "available")
    if mode != "ip":
        for name in ("batctl", "modinfo"):
            check(name, shutil.which(name), shutil.which(name) or "missing")
        code, native = command("modinfo", "-n", "batman_adv")
        selected = os.environ.get("BATADV_NATIVE_MODULE") or native
        build = ROOT / "kernel/batman-adv-emulated-wifi/build" / platform.release()
        if mode == "native":
            code, out = command(str(ROOT / "kernel/batman-adv-emulated-wifi/module-control.sh"), "verify", "native")
            check("native artifact", code == 0, out)
            config = Path("/boot") / ("config-" + platform.release())
            if selected != native:
                config = Path(selected).parent / "build-info.txt"
                expected = "config_batman_adv_batman_v=y"
            else:
                expected = "CONFIG_BATMAN_ADV_BATMAN_V=y"
            try:
                supported = expected in config.read_text().splitlines()
            except OSError:
                supported = False
            check("BATMAN V", supported,
                  f"{config}; if missing, build.sh native and explicitly select BATADV_NATIVE_MODULE={build}/native/batman-adv.ko")
        if mode == "emulated_wifi":
            code, out = command(str(ROOT / "kernel/batman-adv-emulated-wifi/module-control.sh"), "verify", "emulated_wifi")
            check("emulated_wifi artifact", code == 0, out)
            selected = os.environ.get("BATADV_MODULE_ARTIFACT") or str(build / "batman-adv.ko")
        code, signer = command("modinfo", "-F", "signer", selected)
        _, sb = command("mokutil", "--sb-state")
        lockdown = Path("/sys/kernel/security/lockdown")
        try:
            locked = "[integrity]" in lockdown.read_text() or "[confidentiality]" in lockdown.read_text()
        except OSError:
            locked = False
        if "SecureBoot enabled" in sb or locked:
            check("module signature", code == 0 and bool(signer),
                  signer or "unsigned; see setup/README.md (MOK enrollment is a manual step)")
        check("selected module", Path(selected).is_file(), selected)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("ip", "native", "emulated_wifi"), default="ip")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, help="Also save the JSON diagnostic to this file")
    args = parser.parse_args()
    os.chdir(ROOT)
    checks = diagnose(args.mode)
    _, commit = command("git", "-C", str(ROOT), "rev-parse", "HEAD")
    _, dirty = command("git", "-C", str(ROOT), "status", "--porcelain")
    _, compiler = command("g++", "--version")
    result = dict(kernel=platform.release(), python=sys.executable, mode=args.mode,
                  os=dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line),
                  architecture=platform.machine(),
                  repository_commit=commit, repository_dirty=bool(dirty), compiler=compiler,
                  checks=checks, ok=all(c["ok"] for c in checks),
                  note="Read-only checks; signature trust and runtime networking require manual validation.")
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Kernel: {result['kernel']} | Python: {sys.executable} | Mode: {args.mode}")
        for c in checks:
            print(f"[{'OK' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        print(result["note"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
