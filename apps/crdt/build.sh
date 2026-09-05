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

  local rebuild=0
  if [[ ! -x "$output" || "$source" -nt "$output" ]]; then
    rebuild=1
  elif find "$SCRIPT_DIR/common" "$SCRIPT_DIR/usfd" \
      -type f \( -name '*.hpp' -o -name '*.cpp' -o -name '*.cc' \) \
      -newer "$output" -print -quit | grep -q .; then
    rebuild=1
  fi

  if [[ "$rebuild" -eq 1 ]]; then
    echo "[BUILD] $app"
    "$CXX_BIN" "${COMMON_FLAGS[@]}" "$source" "${extra_sources[@]}" \
      -o "$output"
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
else
  build_one "$target"
fi
