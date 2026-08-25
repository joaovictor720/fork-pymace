#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_RELEASE="$(uname -r)"
ARTIFACT="${BATADV_MODULE_ARTIFACT:-$SCRIPT_DIR/build/$KERNEL_RELEASE/batman-adv.ko}"
NATIVE_MODULE="${BATADV_NATIVE_MODULE:-/lib/modules/$KERNEL_RELEASE/kernel/net/batman-adv/batman-adv.ko}"
EXPECTED_VERSION="2019.4-macewifi1"
EXPECTED_NATIVE_VERSION="2019.4"
EXPECTED_UPSTREAM_COMMIT="933568baeba83d6bcaa451656ec1550346f35996"
MODULE_NAME="batman_adv"
PATCH_FILE="$SCRIPT_DIR/patches/0001-batman-adv-add-emulated-wifi-hardif.patch"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

module_loaded() {
  [[ -d "/sys/module/$MODULE_NAME" ]]
}

loaded_version() {
  if [[ -r "/sys/module/$MODULE_NAME/version" ]]; then
    tr -d '\n' < "/sys/module/$MODULE_NAME/version"
  fi
}

loaded_srcversion() {
  if [[ -r "/sys/module/$MODULE_NAME/srcversion" ]]; then
    tr -d '\n' < "/sys/module/$MODULE_NAME/srcversion"
  fi
}

loaded_emulated_wifi() {
  local value

  if [[ ! -r "/sys/module/$MODULE_NAME/parameters/emulated_wifi" ]]; then
    printf 'unsupported'
    return
  fi

  value="$(tr '[:upper:]' '[:lower:]' \
    < "/sys/module/$MODULE_NAME/parameters/emulated_wifi")"
  case "$value" in
    1|y|yes|true|on)
      printf 'true'
      ;;
    0|n|no|false|off)
      printf 'false'
      ;;
    *)
      printf 'unknown:%s' "$value"
      ;;
  esac
}

module_refcount() {
  awk -v name="$MODULE_NAME" '$1 == name { print $3; found = 1 }
    END { if (!found) print 0 }' /proc/modules
}

require_root() {
  if (( EUID != 0 )); then
    exec sudo -- "$0" "$@"
  fi
}

acquire_lock() {
  command -v flock >/dev/null 2>&1 || die "missing command: flock"
  # /run is root-owned and not writable by unprivileged users. Avoid a
  # predictable file directly under the world-writable /run/lock directory.
  exec 9>"/run/mace-batman-adv.lock"
  flock -n 9 || die "another batman-adv module operation is running"
}

load_dependencies() {
  # insmod does not resolve dependencies by itself.
  modprobe libcrc32c || return
  modprobe bridge || return
  modprobe cfg80211 || return
}

validate_module_file() {
  local path="$1"
  local expected_version="$2"
  local label="$3"
  local actual_name actual_version actual_vermagic actual_srcversion

  [[ -f "$path" ]] || die "$label module not found: $path"
  actual_name="$(modinfo -F name "$path")"
  actual_version="$(modinfo -F version "$path")"
  actual_vermagic="$(modinfo -F vermagic "$path" | awk '{print $1}')"
  actual_srcversion="$(modinfo -F srcversion "$path")"
  [[ "$actual_name" == "$MODULE_NAME" ]] || {
    die "$label module name is $actual_name (expected $MODULE_NAME)"
  }
  [[ "$actual_version" == "$expected_version" ]] || {
    die "$label module version is $actual_version (expected $expected_version)"
  }
  [[ "$actual_vermagic" == "$KERNEL_RELEASE" ]] || {
    die "$label module was built for $actual_vermagic, running kernel is $KERNEL_RELEASE"
  }
  [[ -n "$actual_srcversion" ]] || die "$label module has no srcversion"
}

validate_native_module() {
  validate_module_file "$NATIVE_MODULE" "$EXPECTED_NATIVE_VERSION" "native"
  if modinfo -p "$NATIVE_MODULE" | grep -q '^emulated_wifi:'; then
    die "native module unexpectedly exposes emulated_wifi: $NATIVE_MODULE"
  fi
}

validate_artifact() {
  local checksum_file build_info actual_artifact_sha actual_patch_sha
  local info_artifact_sha info_patch_sha info_kernel info_version info_commit

  command -v modinfo >/dev/null 2>&1 || die "missing command: modinfo"
  validate_module_file "$ARTIFACT" "$EXPECTED_VERSION" "experimental"
  modinfo -p "$ARTIFACT" | grep -q '^emulated_wifi:' || {
    die "module does not expose the emulated_wifi parameter"
  }

  checksum_file="$ARTIFACT.sha256"
  [[ -f "$checksum_file" ]] || die "module checksum file is missing: $checksum_file"
  (
    cd "$(dirname "$ARTIFACT")"
    sha256sum --check --status "$(basename "$checksum_file")"
  ) || die "module checksum does not match $checksum_file"

  build_info="$(dirname "$ARTIFACT")/build-info.txt"
  [[ -f "$build_info" ]] || die "module build metadata is missing: $build_info"
  [[ -f "$PATCH_FILE" ]] || die "source patch is missing: $PATCH_FILE"
  actual_artifact_sha="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
  actual_patch_sha="$(sha256sum "$PATCH_FILE" | awk '{print $1}')"
  info_artifact_sha="$(awk -F= '$1 == "artifact_sha256" {print $2}' "$build_info")"
  info_patch_sha="$(awk -F= '$1 == "patch_sha256" {print $2}' "$build_info")"
  info_kernel="$(awk -F= '$1 == "kernel_release" {print $2}' "$build_info")"
  info_version="$(awk -F= '$1 == "module_version" {print $2}' "$build_info")"
  info_commit="$(awk -F= '$1 == "upstream_commit" {print $2}' "$build_info")"
  [[ "$info_artifact_sha" == "$actual_artifact_sha" ]] || {
    die "build-info artifact hash does not match the module"
  }
  [[ "$info_patch_sha" == "$actual_patch_sha" ]] || {
    die "module was not built from the current source patch; rerun build.sh"
  }
  [[ "$info_kernel" == "$KERNEL_RELEASE" ]] || {
    die "build-info kernel is $info_kernel (expected $KERNEL_RELEASE)"
  }
  [[ "$info_version" == "$EXPECTED_VERSION" ]] || {
    die "build-info module version is $info_version (expected $EXPECTED_VERSION)"
  }
  [[ "$info_commit" == "$EXPECTED_UPSTREAM_COMMIT" ]] || {
    die "build-info upstream commit is not the pinned commit"
  }
}

classify_loaded_module() {
  local version flag srcversion native_srcversion artifact_srcversion

  if ! module_loaded; then
    printf 'unloaded'
    return
  fi

  version="$(loaded_version)"
  flag="$(loaded_emulated_wifi)"
  srcversion="$(loaded_srcversion)"
  native_srcversion="$(modinfo -F srcversion "$NATIVE_MODULE" 2>/dev/null || true)"
  artifact_srcversion="$(modinfo -F srcversion "$ARTIFACT" 2>/dev/null || true)"

  if [[ "$version" == "$EXPECTED_NATIVE_VERSION" &&
        "$flag" == "unsupported" &&
        -n "$native_srcversion" && "$srcversion" == "$native_srcversion" ]]; then
    printf 'native'
  elif [[ "$version" == "$EXPECTED_VERSION" &&
          -n "$artifact_srcversion" &&
          "$srcversion" == "$artifact_srcversion" && "$flag" == "true" ]]; then
    printf 'emulated_wifi'
  elif [[ "$version" == "$EXPECTED_VERSION" &&
          -n "$artifact_srcversion" &&
          "$srcversion" == "$artifact_srcversion" && "$flag" == "false" ]]; then
    printf 'experimental_native'
  else
    printf 'unknown'
  fi
}

find_batadv_interfaces() {
  local namespace line

  if command -v ip >/dev/null 2>&1; then
    while IFS= read -r line; do
      [[ -n "$line" ]] && printf 'init: %s\n' "$line"
    done < <(ip -o link show type batadv 2>/dev/null || true)

    while read -r namespace _; do
      [[ -n "$namespace" ]] || continue
      while IFS= read -r line; do
        [[ -n "$line" ]] && printf '%s: %s\n' "$namespace" "$line"
      done < <(ip netns exec "$namespace" ip -o link show type batadv 2>/dev/null || true)
    done < <(ip netns list 2>/dev/null || true)
  fi
}

assert_module_idle() {
  local refs interfaces

  module_loaded || return 0
  interfaces="$(find_batadv_interfaces)"
  [[ -z "$interfaces" ]] || {
    printf '%s\n' "$interfaces" >&2
    die "batman_adv still has mesh interfaces; stop the MACE/CORE session first"
  }
  refs="$(module_refcount)"
  [[ "$refs" =~ ^[0-9]+$ ]] || die "could not read batman_adv reference count"
  (( refs == 0 )) || {
    die "batman_adv is in use (reference count $refs); stop the MACE/CORE session first"
  }
}

unload_if_idle() {
  module_loaded || return 0
  assert_module_idle

  # rmmod will also refuse if an interface or another dependency still uses the
  # module. We deliberately never delete interfaces or kill processes here.
  rmmod "$MODULE_NAME" || {
    die "could not unload batman_adv; check for remaining bat0 interfaces"
  }
}

load_native_exact() {
  load_dependencies || return
  insmod "$NATIVE_MODULE"
}

restore_previous_mode() {
  local previous_mode="$1"

  echo "[WARN] Target module failed to load; attempting to restore $previous_mode" >&2
  case "$previous_mode" in
    native)
      load_native_exact
      ;;
    emulated_wifi)
      load_dependencies || return
      insmod "$ARTIFACT" emulated_wifi=1
      ;;
    experimental_native)
      load_dependencies || return
      insmod "$ARTIFACT" emulated_wifi=0
      ;;
    unloaded)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

restore_and_verify() {
  local previous_mode="$1"
  local restored_mode

  if ! restore_previous_mode "$previous_mode"; then
    echo "[CRITICAL] Failed to restore previous mode: $previous_mode" >&2
    print_status >&2 || true
    return 1
  fi

  restored_mode="$(classify_loaded_module)"
  if [[ "$restored_mode" != "$previous_mode" ]]; then
    echo "[CRITICAL] Rollback requested $previous_mode but produced $restored_mode" >&2
    print_status >&2 || true
    return 1
  fi
}

rollback_after_failed_verification() {
  local previous_mode="$1"

  if module_loaded && ! rmmod "$MODULE_NAME"; then
    echo "[CRITICAL] Verification failed and the unexpected module could not be unloaded" >&2
    print_status >&2 || true
    return 1
  fi
  restore_and_verify "$previous_mode"
}

ensure_native() {
  local state version flag srcversion expected_srcversion

  validate_native_module
  state="$(classify_loaded_module)"
  case "$state" in
    native)
      assert_module_idle
      echo "[OK] Native batman_adv is already loaded ($EXPECTED_NATIVE_VERSION)"
      return 0
      ;;
    emulated_wifi|experimental_native)
      # Rollback may need this exact file if native insertion fails. Validate
      # it before removing the currently working experimental module.
      validate_artifact
      unload_if_idle
      ;;
    unloaded)
      ;;
    unknown)
      die "refusing to replace an unknown loaded batman_adv module"
      ;;
  esac

  if ! load_native_exact; then
    if restore_and_verify "$state"; then
      die "could not load native module; previous mode $state was restored"
    fi
    die "could not load native module and rollback failed; inspect module status"
  fi

  module_loaded || die "insmod returned without loading batman_adv"

  version="$(loaded_version)"
  flag="$(loaded_emulated_wifi)"
  srcversion="$(loaded_srcversion)"
  expected_srcversion="$(modinfo -F srcversion "$NATIVE_MODULE")"
  if [[ "$version" != "$EXPECTED_NATIVE_VERSION" || "$flag" != "unsupported" ||
        "$srcversion" != "$expected_srcversion" ]]; then
    if rollback_after_failed_verification "$state"; then
      die "native verification failed; previous mode $state was restored"
    fi
    die "native verification and rollback failed; inspect module status"
  fi
  echo "[OK] Native batman_adv loaded ($EXPECTED_NATIVE_VERSION)"
}

ensure_emulated_wifi() {
  local state version flag srcversion expected_srcversion

  validate_artifact
  validate_native_module
  state="$(classify_loaded_module)"
  case "$state" in
    emulated_wifi)
      assert_module_idle
      echo "[OK] Emulated-WiFi batman_adv is already loaded"
      return 0
      ;;
    native|experimental_native)
      load_dependencies
      unload_if_idle
      ;;
    unloaded)
      load_dependencies
      ;;
    unknown)
      die "refusing to replace an unknown loaded batman_adv module"
      ;;
  esac

  if ! insmod "$ARTIFACT" emulated_wifi=1; then
    if restore_and_verify "$state"; then
      die "could not load experimental module; previous mode $state was restored"
    fi
    die "could not load experimental module and rollback failed; inspect module status"
  fi

  version="$(loaded_version)"
  flag="$(loaded_emulated_wifi)"
  srcversion="$(loaded_srcversion)"
  expected_srcversion="$(modinfo -F srcversion "$ARTIFACT")"
  if [[ "$version" != "$EXPECTED_VERSION" || "$flag" != "true" ||
        "$srcversion" != "$expected_srcversion" ]]; then
    if rollback_after_failed_verification "$state"; then
      die "experimental verification failed; previous mode $state was restored"
    fi
    die "experimental verification and rollback failed; inspect module status"
  fi
  echo "[OK] Emulated-WiFi batman_adv loaded ($EXPECTED_VERSION)"
}

print_status() {
  local version flag refs state interfaces

  if ! module_loaded; then
    echo "batman_adv: unloaded"
    echo "experimental artifact: $ARTIFACT"
    return 0
  fi

  version="$(loaded_version)"
  flag="$(loaded_emulated_wifi)"
  refs="$(module_refcount)"
  state="$(classify_loaded_module)"
  interfaces="$(find_batadv_interfaces)"
  echo "batman_adv: loaded"
  echo "state: $state"
  echo "version: ${version:-unknown}"
  echo "emulated_wifi: $flag"
  echo "reference_count: $refs"
  if [[ -n "$interfaces" ]]; then
    echo "interfaces:"
    printf '  %s\n' "$interfaces"
  fi
  echo "experimental artifact: $ARTIFACT"
  echo "native module: $NATIVE_MODULE"
}

usage() {
  cat <<EOF
Usage:
  $(basename "$0") status
  $(basename "$0") verify
  $(basename "$0") ensure native|emulated_wifi
  $(basename "$0") rollback

The native mode uses the distro/kernel-installed module. The emulated_wifi
mode loads the repository artifact without installing or replacing that module.
EOF
}

command_name="${1:-}"
case "$command_name" in
  status)
    [[ $# -eq 1 ]] || die "status takes no arguments"
    print_status
    ;;
  verify)
    [[ $# -eq 1 ]] || die "verify takes no arguments"
    validate_native_module
    validate_artifact
    echo "[OK] Native and experimental module files passed validation"
    ;;
  ensure)
    [[ $# -eq 2 ]] || die "ensure requires native or emulated_wifi"
    mode="$2"
    case "$mode" in
      native)
        require_root "$@"
        acquire_lock
        ensure_native
        ;;
      emulated_wifi)
        require_root "$@"
        acquire_lock
        ensure_emulated_wifi
        ;;
      *)
        die "unknown mode: $mode (expected native or emulated_wifi)"
        ;;
    esac
    ;;
  rollback)
    [[ $# -eq 1 ]] || die "rollback takes no arguments"
    require_root "$@"
    acquire_lock
    ensure_native
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
