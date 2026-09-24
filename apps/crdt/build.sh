#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CXX_BIN="${CXX:-g++}"
COMMON_FLAGS=(-std=c++17 -O2 -pthread -Wall -Wextra -Wpedantic)

build_one() {
  local app="$1"
  local source
  local output
  local -a extra_sources=()
  local fingerprint stamp

  case "$app" in
    broadcast|multiunicast|usfd|usfdx1|usfdx3)
      source="$SCRIPT_DIR/$app/crdt_$app.cpp"
      output="$SCRIPT_DIR/$app/crdt_$app"
      ;;
    rapid)
      source="$SCRIPT_DIR/rapid/crdt_rapid.cpp"
      output="$SCRIPT_DIR/rapid/crdt_rapid"
      extra_sources+=("$SCRIPT_DIR/common/lib_gossip/rapid/rapid.cpp")
      ;;
    trickle)
      source="$SCRIPT_DIR/trickle/crdt_trickle.cpp"
      output="$SCRIPT_DIR/trickle/crdt_trickle"
      extra_sources+=("$SCRIPT_DIR/common/lib_gossip/trickle/trickle.cpp")
      ;;
    *)
      echo "unknown CRDT application: $app" >&2
      return 2
      ;;
  esac

  # Each host builds its own binaries. Reuse requires matching sources,
  # compiler, architecture, flags and output bytes, not just timestamps.
  mkdir -p "$SCRIPT_DIR/../../.build/crdt"
  stamp="$SCRIPT_DIR/../../.build/crdt/$app.sha256"
  fingerprint="$({
    "$CXX_BIN" --version
    uname -m
    printf '%s\n' "${COMMON_FLAGS[@]}"
    find "$SCRIPT_DIR" -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.cc' \) \
      -print0 | sort -z | xargs -0 sha256sum
  } | sha256sum | cut -d ' ' -f 1)"
  local rebuild=0
  if [[ ! -x "$output" || ! -f "$stamp" || "$(head -n 1 "$stamp")" != "$fingerprint" ]]; then
    rebuild=1
  elif [[ "$(tail -n 1 "$stamp")" != "$(sha256sum "$output" | cut -d ' ' -f 1)" ]]; then
    rebuild=1
  fi

  if [[ "$rebuild" -eq 1 ]]; then
    echo "[BUILD] $app"
    "$CXX_BIN" "${COMMON_FLAGS[@]}" "$source" "${extra_sources[@]}" \
      -o "$output"
    printf '%s\n' "$fingerprint" "$(sha256sum "$output" | cut -d ' ' -f 1)" > "$stamp"
  else
    echo "[BUILD] $app is up to date"
  fi
}

target="${1:-all}"
if [[ "$target" == "all" ]]; then
  for app in broadcast multiunicast rapid trickle usfd usfdx1 usfdx3; do
    build_one "$app"
  done
elif [[ "$target" == "test" ]]; then
  test_binary="${TMPDIR:-/tmp}/mace_spatial_coverage_test"
  rapid_test_binary="${TMPDIR:-/tmp}/mace_rapid_test"
  trickle_test_binary="${TMPDIR:-/tmp}/mace_trickle_test"
  "$CXX_BIN" "${COMMON_FLAGS[@]}" \
    "$SCRIPT_DIR/common/spatial_coverage_test.cpp" -o "$test_binary"
  "$test_binary"
  "$CXX_BIN" "${COMMON_FLAGS[@]}" \
    "$SCRIPT_DIR/common/lib_gossip/rapid/rapid_test.cpp" \
    "$SCRIPT_DIR/common/lib_gossip/rapid/rapid.cpp" \
    -o "$rapid_test_binary"
  "$rapid_test_binary"
  "$CXX_BIN" "${COMMON_FLAGS[@]}" \
    "$SCRIPT_DIR/common/lib_gossip/trickle/trickle_test.cpp" \
    "$SCRIPT_DIR/common/lib_gossip/trickle/trickle.cpp" \
    -o "$trickle_test_binary"
  "$trickle_test_binary"
  spatial_trickle_test_binary="${TMPDIR:-/tmp}/mace_spatial_trickle_test"
  "$CXX_BIN" "${COMMON_FLAGS[@]}" \
    "$SCRIPT_DIR/trickle/spatial_adapter_test.cpp" \
    "$SCRIPT_DIR/common/lib_gossip/trickle/trickle.cpp" \
    -o "$spatial_trickle_test_binary"
  "$spatial_trickle_test_binary"
else
  build_one "$target"
fi
