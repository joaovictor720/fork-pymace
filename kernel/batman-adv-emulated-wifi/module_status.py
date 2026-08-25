#!/usr/bin/env python3
"""Write reproducibility metadata for the selected batman-adv module."""

import argparse
import datetime as dt
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY = SCRIPT_DIR.parent.parent
PATCH = SCRIPT_DIR / "patches" / "0001-batman-adv-add-emulated-wifi-hardif.patch"
UPSTREAM_URL = "https://github.com/open-mesh-mirror/batman-adv.git"
UPSTREAM_TAG = "v2019.4"
UPSTREAM_COMMIT = "933568baeba83d6bcaa451656ec1550346f35996"


def run(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def key_value_file(path: Path) -> Optional[Dict[str, str]]:
    content = read(path)
    if content is None:
        return None
    result: Dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def modinfo(path_or_name: str, field: str) -> Optional[str]:
    return run("modinfo", "-F", field, path_or_name)


def normalize_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "y", "yes", "true", "on"}:
        return True
    if normalized in {"0", "n", "no", "false", "off"}:
        return False
    return None


def module_refcount() -> Optional[int]:
    try:
        for line in Path("/proc/modules").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if fields and fields[0] == "batman_adv":
                return int(fields[2])
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        pass
    return None


def git_metadata() -> Dict[str, Any]:
    commit = run("git", "-C", str(REPOSITORY), "rev-parse", "HEAD")
    status = run("git", "-C", str(REPOSITORY), "status", "--porcelain")
    return {
        "commit": commit,
        "dirty": None if status is None else bool(status),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--requested-mode",
        choices=("native", "emulated_wifi"),
        default=None,
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=SCRIPT_DIR / "build" / platform.release() / "batman-adv.ko",
    )
    parser.add_argument(
        "--require-match",
        action="store_true",
        help="exit unsuccessfully unless requested and effective modes match",
    )
    args = parser.parse_args()
    if args.require_match and args.requested_mode is None:
        parser.error("--require-match requires --requested-mode")

    kernel_release = platform.release()
    artifact = args.artifact.resolve()
    native_path = (
        Path("/lib/modules")
        / kernel_release
        / "kernel/net/batman-adv/batman-adv.ko"
    )
    build_info = artifact.parent / "build-info.txt"
    sys_module = Path("/sys/module/batman_adv")
    loaded = sys_module.is_dir()
    loaded_version = read(sys_module / "version") if loaded else None
    loaded_srcversion = read(sys_module / "srcversion") if loaded else None
    emulated_raw = read(sys_module / "parameters" / "emulated_wifi") if loaded else None
    emulated = normalize_bool(emulated_raw)
    native_srcversion = (
        modinfo(str(native_path), "srcversion") if native_path.is_file() else None
    )
    artifact_srcversion = (
        modinfo(str(artifact), "srcversion") if artifact.is_file() else None
    )

    if not loaded:
        effective_mode = "unloaded"
    elif (
        loaded_version == "2019.4-macewifi1"
        and artifact_srcversion is not None
        and loaded_srcversion == artifact_srcversion
        and emulated is True
    ):
        effective_mode = "emulated_wifi"
    elif (
        loaded_version == "2019.4-macewifi1"
        and artifact_srcversion is not None
        and loaded_srcversion == artifact_srcversion
        and emulated is False
    ):
        effective_mode = "experimental_native"
    elif (
        loaded_version == "2019.4"
        and native_srcversion is not None
        and loaded_srcversion == native_srcversion
        and emulated_raw is None
    ):
        effective_mode = "native"
    else:
        effective_mode = "unknown"

    data: Dict[str, Any] = {
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "requested_mode": args.requested_mode,
        "effective_mode": effective_mode,
        "mode_matches_request": (
            None
            if args.requested_mode is None
            else effective_mode == args.requested_mode
        ),
        "kernel_release": kernel_release,
        "batctl_version": run("batctl", "-v"),
        "loaded_module": {
            "loaded": loaded,
            "version": loaded_version,
            "srcversion": loaded_srcversion,
            "emulated_wifi_parameter_raw": emulated_raw,
            "emulated_wifi": emulated,
            "reference_count": module_refcount() if loaded else None,
        },
        "experimental_artifact": {
            "path": str(artifact),
            "exists": artifact.is_file(),
            "sha256": sha256(artifact),
            "name": modinfo(str(artifact), "name") if artifact.is_file() else None,
            "version": (
                modinfo(str(artifact), "version") if artifact.is_file() else None
            ),
            "srcversion": artifact_srcversion,
            "vermagic": (
                modinfo(str(artifact), "vermagic") if artifact.is_file() else None
            ),
            "build_info": key_value_file(build_info),
        },
        "installed_native_module": {
            "path": str(native_path),
            "exists": native_path.is_file(),
            "sha256": sha256(native_path),
            "name": (
                modinfo(str(native_path), "name") if native_path.is_file() else None
            ),
            "version": (
                modinfo(str(native_path), "version") if native_path.is_file() else None
            ),
            "srcversion": native_srcversion,
            "vermagic": (
                modinfo(str(native_path), "vermagic") if native_path.is_file() else None
            ),
            "signer": (
                modinfo(str(native_path), "signer") if native_path.is_file() else None
            ),
        },
        "patch": {
            "path": str(PATCH),
            "sha256": sha256(PATCH),
        },
        "upstream": {
            "url": UPSTREAM_URL,
            "tag": UPSTREAM_TAG,
            "commit": UPSTREAM_COMMIT,
        },
        "mace_repository": git_metadata(),
    }

    print(json.dumps(data, indent=2, sort_keys=True))
    if args.require_match and effective_mode != args.requested_mode:
        raise SystemExit(
            "[ERROR] requested batman-adv mode "
            f"{args.requested_mode!r}, effective mode is {effective_mode!r}"
        )


if __name__ == "__main__":
    main()
