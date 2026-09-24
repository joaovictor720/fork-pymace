#!/usr/bin/env bash
# File signing only. This script never enrolls a key or inserts a module.
set -euo pipefail
[[ $# -eq 3 ]] || { echo "Usage: $0 <module.ko> <private-key.pem> <certificate.der>" >&2; exit 2; }
ARTIFACT="$(realpath "$1")"
KEY="$(realpath "$2")"
CERT="$(realpath "$3")"
KERNEL_RELEASE="$(modinfo -F vermagic "$ARTIFACT" | awk '{print $1}')"
INFO="$(dirname "$ARTIFACT")/build-info.txt"
[[ -f "$INFO" && -f "$ARTIFACT.sha256" ]] || { echo "Missing build metadata/checksum" >&2; exit 1; }
(cd "$(dirname "$ARTIFACT")" && sha256sum --check --status "$(basename "$ARTIFACT").sha256")
"/lib/modules/$KERNEL_RELEASE/build/scripts/sign-file" sha256 "$KEY" "$CERT" "$ARTIFACT"
# Signing changes the artifact bytes. Preserve the original build digest too.
python3 - "$ARTIFACT" "$INFO" <<'PY'
import hashlib, pathlib, sys
p, info = map(pathlib.Path, sys.argv[1:])
data = dict(line.split("=", 1) for line in info.read_text().splitlines() if "=" in line)
data.setdefault("unsigned_artifact_sha256", data["artifact_sha256"])
data["artifact_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
info.write_text("".join(f"{k}={v}\n" for k, v in data.items()))
pathlib.Path(str(p) + ".sha256").write_text(f'{data["artifact_sha256"]}  {p.name}\n')
PY
echo "[OK] Signed file; certificate must still be trusted by the running kernel."
modinfo -F signer "$ARTIFACT"
