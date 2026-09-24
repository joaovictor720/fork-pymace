#!/usr/bin/env bash
# Online setup. Privileged operations are limited to the explicit apt phase.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_DIR="$ROOT_DIR/setup"
BATMAN_DIR="$ROOT_DIR/kernel/batman-adv-emulated-wifi"
source "$SETUP_DIR/versions.env"
source "$SETUP_DIR/sources.sh"
MODE=none
SKIP_SYSTEM=0
SYSTEM_ONLY=0
CHECK=0
YES=0
REBUILD=0
die() { echo "[ERROR] $*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: ./setup/setup.sh [options]
  --batman none|native|emulated_wifi  Optional kernel module (default: none)
  --skip-system                     Use already installed system dependencies
  --check                           Print the plan without changing anything
  --yes                             Accept apt package installation
  --rebuild                         Rebuild CORE and the selected module
  --system-only                     Install system packages only
  -h, --help                        Show this help

Default: Ubuntu 24.04 packages via sudo apt, Python 3.12 environment, CORE 9.2.0.
Sources are downloaded and pinned; builds remain inside the checkout.
No module loading, network configuration, key enrollment, DKMS, or reboot.
Run as a normal user. Signing instructions: setup/README.md.
EOF
}
while (($#)); do
  case "$1" in
    --batman) [[ $# -ge 2 ]] || die "--batman requires a mode"; MODE="$2"; shift 2 ;;
    --skip-system) SKIP_SYSTEM=1; shift ;;
    --system-only) SYSTEM_ONLY=1; shift ;;
    --check) CHECK=1; shift ;;
    --yes) YES=1; shift ;;
    --rebuild) REBUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done
case "$MODE" in none|native|emulated_wifi) ;; *) die "Invalid BATMAN mode: $MODE" ;; esac
(( !SKIP_SYSTEM || !SYSTEM_ONLY )) || die "--skip-system and --system-only conflict"
[[ "$(uname -s)" == Linux ]] || die "MACE requires Linux"
KERNEL_RELEASE="$(uname -r)"
PACKAGES=(python3-venv python3-dev build-essential autoconf automake libtool
  pkg-config libev-dev git iproute2 nftables ethtool tcpdump tshark
  nlohmann-json3-dev netcat-openbsd iputils-ping util-linux)
if [[ "$MODE" != none ]]; then
  SCRIPT_DIR="$BATMAN_DIR"
  source "$BATMAN_DIR/profile.sh"
  PACKAGES+=(batctl kmod mokutil openssl "linux-headers-$KERNEL_RELEASE")
fi
if ((!SKIP_SYSTEM)); then
  source /etc/os-release
  [[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] ||
    die "Automatic package installation supports Ubuntu 24.04. Install dependencies manually and use --skip-system; see setup/README.md."
fi
echo "[PLAN] Python $PYTHON_VERSION / CORE $CORE_VERSION; BATMAN: $MODE; kernel: $KERNEL_RELEASE"
if ((SKIP_SYSTEM)); then
  echo "[PLAN] System dependencies must already be installed (--skip-system)."
else
  printf '[PLAN] apt packages:'; printf ' %s' "${PACKAGES[@]}"; printf '\n'
fi
echo "[PLAN] Local environment: $ROOT_DIR/.venv; source cache: $ROOT_DIR/.build/sources"
if [[ "$MODE" != none ]]; then
  echo "[PLAN] batman-adv $UPSTREAM_TAG ($UPSTREAM_COMMIT); signing/enrollment/loading are separate manual steps."
fi
((!REBUILD)) || echo "[PLAN] --rebuild replaces the selected module file; a new signature will be needed."
((!CHECK)) || exit 0
((EUID != 0)) || die "Run setup as your normal user; only apt uses sudo."
mkdir -p "$ROOT_DIR/.build/setup"
exec 9>"$ROOT_DIR/.build/setup/lock"
flock -n 9 || die "Another setup is running for this checkout"
if ((!SKIP_SYSTEM)); then
  missing=()
  for package in "${PACKAGES[@]}"; do
    if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]]; then
      missing+=("$package")
    fi
  done
  if ((${#missing[@]})); then
    echo "[INFO] Installing missing system packages via sudo apt."
    sudo apt-get update
    apt_options=()
    ((!YES)) || apt_options+=(-y)
    sudo apt-get install "${apt_options[@]}" "${missing[@]}"
  else
    echo "[OK] System packages already installed."
  fi
fi
((!SYSTEM_ONLY)) || exit 0
export PYTHONDONTWRITEBYTECODE=1
export MACE_SETUP_REBUILD="$REBUILD"
"$SETUP_DIR/python-core.sh"
if [[ "$MODE" != none ]]; then
  # Select the repository build explicitly, without replacing distro modules.
  artifact_dir="$BATMAN_DIR/build/$KERNEL_RELEASE"
  if [[ "$MODE" == native ]]; then
    artifact_dir+=/native
    export BATADV_NATIVE_MODULE="$artifact_dir/batman-adv.ko"
  else
    export BATADV_MODULE_ARTIFACT="$artifact_dir/batman-adv.ko"
  fi
  if ((!REBUILD)) && "$BATMAN_DIR/module-control.sh" verify "$MODE" >/dev/null 2>&1; then
    echo "[OK] Reusing validated $MODE artifact (existing signature preserved)."
  else
    source_cache="$ROOT_DIR/.build/sources/batman-adv-$UPSTREAM_TAG"
    fetch_source "$source_cache" https://github.com/open-mesh-mirror/batman-adv.git "$UPSTREAM_TAG" "$UPSTREAM_COMMIT"
    BATADV_SOURCE_DIR="$source_cache" OUTPUT_DIR="$BATMAN_DIR/build/$KERNEL_RELEASE" \
      KERNEL_RELEASE="$KERNEL_RELEASE" "$BATMAN_DIR/build.sh" "$MODE"
    "$BATMAN_DIR/module-control.sh" verify "$MODE"
  fi
  if [[ "$MODE" == native ]]; then
    # Plain path, never shell code. runtime.sh only reads the current kernel's file.
    printf '%s\n' "$BATADV_NATIVE_MODULE" > "$ROOT_DIR/.build/setup/native-module-$KERNEL_RELEASE"
  fi
  echo "[INFO] Module prepared: $artifact_dir/batman-adv.ko"
  echo "[INFO] If signing is required, follow setup/README.md, then rerun this same setup command."
fi
doctor_mode="$MODE"
[[ "$MODE" != none ]] || doctor_mode=ip
status=0
"$SETUP_DIR/doctor.sh" --mode "$doctor_mode" --output "$ROOT_DIR/.build/setup/diagnostic-$doctor_mode.json" || status=$?
if ((status)); then
  echo "[PENDING] Build steps completed; resolve the failed checks above and rerun setup. See setup/README.md." >&2
  exit 2
fi
echo "[OK] Setup checks passed. Privileged network/experiment tests and kernel signature trust still require validation."
