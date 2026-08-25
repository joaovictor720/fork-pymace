#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM_URL="https://github.com/open-mesh-mirror/batman-adv.git"
UPSTREAM_TAG="v2019.4"
UPSTREAM_COMMIT="933568baeba83d6bcaa451656ec1550346f35996"
MODULE_VERSION="2019.4-macewifi1"
PATCH_FILE="$SCRIPT_DIR/patches/0001-batman-adv-add-emulated-wifi-hardif.patch"

KERNEL_RELEASE="${KERNEL_RELEASE:-$(uname -r)}"
KERNEL_DIR="${KERNEL_DIR:-/lib/modules/$KERNEL_RELEASE/build}"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/build/$KERNEL_RELEASE}"
BUILD_JOBS="${BUILD_JOBS:-2}"
BUILD_TMP_BASE="${TMPDIR:-/tmp}"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

for command in git make gcc strip modinfo sha256sum; do
  command -v "$command" >/dev/null 2>&1 || die "missing command: $command"
done

[[ -d "$KERNEL_DIR" ]] || die "kernel build directory not found: $KERNEL_DIR"
[[ -f "$KERNEL_DIR/Module.symvers" ]] || die "missing Module.symvers in $KERNEL_DIR"
[[ -f "$KERNEL_DIR/.config" ]] || die "missing kernel config in $KERNEL_DIR"
[[ -f "$PATCH_FILE" ]] || die "patch not found: $PATCH_FILE"
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || die "BUILD_JOBS must be a positive integer"

available_kb="$(df -Pk "$BUILD_TMP_BASE" | awk 'NR == 2 {print $4}')"
[[ "$available_kb" =~ ^[0-9]+$ ]] || die "could not determine free disk space"
(( available_kb >= 204800 )) || die "at least 200 MiB free space is required"

build_tmp="$(mktemp -d "$BUILD_TMP_BASE/batadv-macewifi.XXXXXX")"
cleanup() {
  if [[ -n "${build_tmp:-}" && -d "$build_tmp" && "$build_tmp" == "$BUILD_TMP_BASE"/batadv-macewifi.* ]]; then
    rm -rf -- "$build_tmp"
  fi
}
trap cleanup EXIT

source_dir="$build_tmp/batman-adv"
echo "[INFO] Cloning batman-adv $UPSTREAM_TAG"
git -c advice.detachedHead=false clone --quiet --branch "$UPSTREAM_TAG" \
  --depth 1 "$UPSTREAM_URL" "$source_dir"

actual_commit="$(git -C "$source_dir" rev-parse HEAD)"
[[ "$actual_commit" == "$UPSTREAM_COMMIT" ]] || {
  die "unexpected upstream commit: $actual_commit (expected $UPSTREAM_COMMIT)"
}

git -C "$source_dir" apply --check "$PATCH_FILE"
git -C "$source_dir" apply "$PATCH_FILE"

kernel_option() {
  local option="$1"
  if grep -q "^${option}=y$" "$KERNEL_DIR/.config"; then
    printf 'y'
  else
    printf 'n'
  fi
}

echo "[INFO] Building $MODULE_VERSION for $KERNEL_RELEASE"
reproducible_cflags="-fmacro-prefix-map=$source_dir=batman-adv -fdebug-prefix-map=$source_dir=batman-adv"
make -C "$source_dir" -j"$BUILD_JOBS" \
  KERNELPATH="$KERNEL_DIR" \
  REVISION="$MODULE_VERSION" \
  KCFLAGS="$reproducible_cflags" \
  CONFIG_BATMAN_ADV_DEBUG="$(kernel_option CONFIG_BATMAN_ADV_DEBUG)" \
  CONFIG_BATMAN_ADV_DEBUGFS="$(kernel_option CONFIG_BATMAN_ADV_DEBUGFS)" \
  CONFIG_BATMAN_ADV_BLA="$(kernel_option CONFIG_BATMAN_ADV_BLA)" \
  CONFIG_BATMAN_ADV_DAT="$(kernel_option CONFIG_BATMAN_ADV_DAT)" \
  CONFIG_BATMAN_ADV_NC="$(kernel_option CONFIG_BATMAN_ADV_NC)" \
  CONFIG_BATMAN_ADV_MCAST="$(kernel_option CONFIG_BATMAN_ADV_MCAST)" \
  CONFIG_BATMAN_ADV_SYSFS="$(kernel_option CONFIG_BATMAN_ADV_SYSFS)" \
  CONFIG_BATMAN_ADV_TRACING="$(kernel_option CONFIG_BATMAN_ADV_TRACING)" \
  CONFIG_BATMAN_ADV_BATMAN_V="$(kernel_option CONFIG_BATMAN_ADV_BATMAN_V)"

source_module="$source_dir/net/batman-adv/batman-adv.ko"
[[ -f "$source_module" ]] || die "build did not produce $source_module"

mkdir -p "$OUTPUT_DIR"
artifact="$OUTPUT_DIR/batman-adv.ko"
install -m 0644 "$source_module" "$artifact"
strip --strip-debug "$artifact"

actual_version="$(modinfo -F version "$artifact")"
actual_vermagic="$(modinfo -F vermagic "$artifact" | awk '{print $1}')"
modinfo -p "$artifact" | grep -q '^emulated_wifi:' || die "module parameter is missing"
[[ "$actual_version" == "$MODULE_VERSION" ]] || die "unexpected module version: $actual_version"
[[ "$actual_vermagic" == "$KERNEL_RELEASE" ]] || {
  die "vermagic mismatch: $actual_vermagic (expected $KERNEL_RELEASE)"
}

artifact_sha256="$(sha256sum "$artifact" | awk '{print $1}')"
patch_sha256="$(sha256sum "$PATCH_FILE" | awk '{print $1}')"
printf '%s  %s\n' "$artifact_sha256" "$(basename "$artifact")" > "$artifact.sha256"
printf '%s\n' \
  "module_version=$MODULE_VERSION" \
  "kernel_release=$KERNEL_RELEASE" \
  "upstream_tag=$UPSTREAM_TAG" \
  "upstream_commit=$UPSTREAM_COMMIT" \
  "patch_sha256=$patch_sha256" \
  "artifact_sha256=$artifact_sha256" \
  "compiler=$(gcc -dumpfullversion -dumpversion)" \
  "config_hz=$(sed -n 's/^CONFIG_HZ=//p' "$KERNEL_DIR/.config")" \
  "config_batman_adv_debug=$(kernel_option CONFIG_BATMAN_ADV_DEBUG)" \
  "config_batman_adv_debugfs=$(kernel_option CONFIG_BATMAN_ADV_DEBUGFS)" \
  "config_batman_adv_bla=$(kernel_option CONFIG_BATMAN_ADV_BLA)" \
  "config_batman_adv_dat=$(kernel_option CONFIG_BATMAN_ADV_DAT)" \
  "config_batman_adv_nc=$(kernel_option CONFIG_BATMAN_ADV_NC)" \
  "config_batman_adv_mcast=$(kernel_option CONFIG_BATMAN_ADV_MCAST)" \
  "config_batman_adv_sysfs=$(kernel_option CONFIG_BATMAN_ADV_SYSFS)" \
  "config_batman_adv_tracing=$(kernel_option CONFIG_BATMAN_ADV_TRACING)" \
  "config_batman_adv_batman_v=$(kernel_option CONFIG_BATMAN_ADV_BATMAN_V)" \
  > "$OUTPUT_DIR/build-info.txt"

echo "[OK] Built $artifact"
echo "[OK] SHA-256: $artifact_sha256"
