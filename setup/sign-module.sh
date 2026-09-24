#!/usr/bin/env bash
# Explicit local-file signing. Does not create/enroll keys or load a module.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BATMAN_DIR="$ROOT_DIR/kernel/batman-adv-emulated-wifi"
[[ $# == 3 ]] || {
  echo "Usage: $0 native|emulated_wifi private-key.pem certificate.der" >&2; exit 2;
}
artifact="$BATMAN_DIR/build/$(uname -r)/batman-adv.ko"
case "$1" in
  native)
    artifact="$BATMAN_DIR/build/$(uname -r)/native/batman-adv.ko"
    export BATADV_NATIVE_MODULE="$artifact"
    ;;
  emulated_wifi) export BATADV_MODULE_ARTIFACT="$artifact" ;;
  *) echo "Expected native or emulated_wifi" >&2; exit 2 ;;
esac
"$BATMAN_DIR/module-control.sh" verify "$1"
exec "$BATMAN_DIR/sign-module.sh" "$artifact" "$2" "$3"
