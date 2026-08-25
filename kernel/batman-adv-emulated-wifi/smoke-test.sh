#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL="$SCRIPT_DIR/module-control.sh"
MODE="${1:-both}"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

case "$MODE" in
  native|emulated_wifi|both)
    ;;
  *)
    die "usage: $(basename "$0") [native|emulated_wifi|both]"
    ;;
esac

if (( EUID != 0 )); then
  exec sudo -- "$0" "$@"
fi

for command in ip batctl tshark nc ping sysctl flock modinfo; do
  command -v "$command" >/dev/null 2>&1 || die "missing command: $command"
done
[[ -x "$CONTROL" ]] || die "module controller is not executable: $CONTROL"
exec 8>"/run/mace-batman-adv-smoke.lock"
flock -n 8 || die "another batman-adv smoke test is running"
"$CONTROL" verify
KERNEL_RELEASE="$(uname -r)"
NATIVE_MODULE="/lib/modules/$KERNEL_RELEASE/kernel/net/batman-adv/batman-adv.ko"
ARTIFACT="$SCRIPT_DIR/build/$KERNEL_RELEASE/batman-adv.ko"
NATIVE_SRCVERSION="$(modinfo -F srcversion "$NATIVE_MODULE")"
ARTIFACT_SRCVERSION="$(modinfo -F srcversion "$ARTIFACT")"

suffix="$$"
NS1="mw1-$suffix"
NS2="mw2-$suffix"
BRIDGE="mwb$suffix"
HOST1="mw1h$suffix"
HOST2="mw2h$suffix"
PEER1="mw1n$suffix"
PEER2="mw2n$suffix"
TMP_DIR=""
CAPTURE_PID=""
NS1_CREATED=0
NS2_CREATED=0
VETH1_CREATED=0
VETH2_CREATED=0
BRIDGE_CREATED=0

initial_mode="unloaded"
initial_routing_algo=""
if [[ -d /sys/module/batman_adv ]]; then
  initial_version="$(tr -d '\n' < /sys/module/batman_adv/version)"
  initial_srcversion="$(tr -d '\n' < /sys/module/batman_adv/srcversion)"
  initial_refs="$(awk '$1 == "batman_adv" {print $3}' /proc/modules)"
  [[ "${initial_refs:-}" =~ ^[0-9]+$ ]] || die "cannot read initial module reference count"
  (( initial_refs == 0 )) || {
    die "batman_adv is already in use (reference count $initial_refs)"
  }

  if [[ ! -e /sys/module/batman_adv/parameters/emulated_wifi &&
        "$initial_version" == "2019.4" &&
        "$initial_srcversion" == "$NATIVE_SRCVERSION" ]]; then
    initial_mode="native"
  elif [[ -r /sys/module/batman_adv/parameters/emulated_wifi &&
          "$initial_version" == "2019.4-macewifi1" &&
          "$initial_srcversion" == "$ARTIFACT_SRCVERSION" ]]; then
    initial_flag="$(tr '[:lower:]' '[:upper:]' \
      < /sys/module/batman_adv/parameters/emulated_wifi)"
    if [[ "$initial_flag" == "Y" || "$initial_flag" == "1" ]]; then
      initial_mode="emulated_wifi"
    else
      die "smoke test does not alter an experimental module loaded with emulated_wifi=0"
    fi
  else
    die "smoke test refuses to alter an unknown loaded batman_adv module"
  fi

  initial_routing_algo="$(batctl routing_algo)"
  case "$initial_routing_algo" in
    BATMAN_IV|BATMAN_V)
      ;;
    *)
      die "unknown initial BATMAN routing algorithm: $initial_routing_algo"
      ;;
  esac
fi

netns_exists() {
  ip netns list 2>/dev/null |
    awk -v name="$1" '$1 == name {found = 1} END {exit !found}'
}

link_exists() {
  ip link show dev "$1" >/dev/null 2>&1
}

teardown_network() {
  local teardown_rc=0

  if (( NS1_CREATED )); then
    ip netns del "$NS1" >/dev/null 2>&1 || true
    if netns_exists "$NS1"; then
      teardown_rc=1
    else
      NS1_CREATED=0
    fi
  fi
  if (( NS2_CREATED )); then
    ip netns del "$NS2" >/dev/null 2>&1 || true
    if netns_exists "$NS2"; then
      teardown_rc=1
    else
      NS2_CREATED=0
    fi
  fi

  # Deleting a namespace normally deletes its VETH pair. Explicit deletion is
  # still needed for failures that occurred before the peer was moved.
  if (( VETH1_CREATED )); then
    ip link del "$HOST1" >/dev/null 2>&1 || true
    if link_exists "$HOST1"; then
      teardown_rc=1
    else
      VETH1_CREATED=0
    fi
  fi
  if (( VETH2_CREATED )); then
    ip link del "$HOST2" >/dev/null 2>&1 || true
    if link_exists "$HOST2"; then
      teardown_rc=1
    else
      VETH2_CREATED=0
    fi
  fi
  if (( BRIDGE_CREATED )); then
    ip link del "$BRIDGE" >/dev/null 2>&1 || true
    if link_exists "$BRIDGE"; then
      teardown_rc=1
    else
      BRIDGE_CREATED=0
    fi
  fi

  return "$teardown_rc"
}

restore_initial_module() {
  local restore_rc=0
  local current_version current_srcversion current_flag current_known refs

  case "$initial_mode" in
    native)
      "$CONTROL" ensure native || restore_rc=$?
      ;;
    emulated_wifi)
      "$CONTROL" ensure emulated_wifi || restore_rc=$?
      ;;
    unloaded)
      if [[ -d /sys/module/batman_adv ]]; then
        current_version="$(tr -d '\n' < /sys/module/batman_adv/version)"
        current_srcversion="$(tr -d '\n' < /sys/module/batman_adv/srcversion)"
        current_flag="unsupported"
        if [[ -r /sys/module/batman_adv/parameters/emulated_wifi ]]; then
          current_flag="$(tr '[:lower:]' '[:upper:]' \
            < /sys/module/batman_adv/parameters/emulated_wifi)"
        fi
        current_known=0
        if [[ "$current_version" == "2019.4" &&
              "$current_srcversion" == "$NATIVE_SRCVERSION" &&
              "$current_flag" == "unsupported" ]]; then
          current_known=1
        elif [[ "$current_version" == "2019.4-macewifi1" &&
                "$current_srcversion" == "$ARTIFACT_SRCVERSION" &&
                ( "$current_flag" == "Y" || "$current_flag" == "1" ) ]]; then
          current_known=1
        fi
        if (( ! current_known )); then
          echo "[ERROR] Refusing to unload an unexpected batman_adv module" >&2
          restore_rc=1
          return "$restore_rc"
        fi
        refs="$(awk '$1 == "batman_adv" {print $3}' /proc/modules)"
        if [[ "$refs" == "0" ]]; then
          rmmod batman_adv || restore_rc=$?
        else
          echo "[WARN] Leaving batman_adv loaded because its reference count is $refs" >&2
          restore_rc=1
        fi
      fi
      ;;
  esac
  if (( restore_rc == 0 )) && [[ -n "$initial_routing_algo" ]]; then
    batctl routing_algo "$initial_routing_algo" || restore_rc=$?
  fi
  return "$restore_rc"
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ -n "$CAPTURE_PID" ]] && kill -0 "$CAPTURE_PID" 2>/dev/null; then
    kill "$CAPTURE_PID" >/dev/null 2>&1 || true
    wait "$CAPTURE_PID" >/dev/null 2>&1 || true
  fi
  CAPTURE_PID=""
  if ! teardown_network; then
    echo "[ERROR] Could not remove every smoke-test network resource" >&2
    (( rc == 0 )) && rc=1
  fi
  if ! restore_initial_module; then
    echo "[ERROR] Could not restore the initial batman_adv module state" >&2
    (( rc == 0 )) && rc=1
  fi
  if [[ -n "$TMP_DIR" && "$TMP_DIR" == /tmp/batadv-macewifi-smoke.* &&
        -d "$TMP_DIR" ]]; then
    rm -rf -- "$TMP_DIR"
  fi
  exit "$rc"
}
TMP_DIR="$(mktemp -d "/tmp/batadv-macewifi-smoke.XXXXXX")"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

setup_network() {
  local subnet="$1"

  ip netns add "$NS1"
  NS1_CREATED=1
  ip netns add "$NS2"
  NS2_CREATED=1
  ip link add "$HOST1" type veth peer name "$PEER1"
  VETH1_CREATED=1
  ip link add "$HOST2" type veth peer name "$PEER2"
  VETH2_CREATED=1
  ip link add "$BRIDGE" type bridge
  BRIDGE_CREATED=1

  ip link set "$PEER1" netns "$NS1"
  ip link set "$PEER2" netns "$NS2"
  ip -n "$NS1" link set "$PEER1" name eth0
  ip -n "$NS2" link set "$PEER2" name eth0

  ip link set "$HOST1" master "$BRIDGE"
  ip link set "$HOST2" master "$BRIDGE"
  ip link set "$HOST1" up
  ip link set "$HOST2" up
  ip link set "$BRIDGE" up

  for ns in "$NS1" "$NS2"; do
    ip netns exec "$ns" sysctl -q -w net.ipv6.conf.all.disable_ipv6=1
    ip netns exec "$ns" sysctl -q -w net.ipv6.conf.default.disable_ipv6=1
    ip -n "$ns" link set lo up
    ip -n "$ns" link set eth0 up
    ip -n "$ns" link add name bat0 type batadv
    ip netns exec "$ns" batctl meshif bat0 interface add eth0
    ip -n "$ns" link set bat0 up
  done

  ip -n "$NS1" addr add "10.254.$subnet.1/24" dev bat0
  ip -n "$NS2" addr add "10.254.$subnet.2/24" dev bat0
}

wait_for_neighbor() {
  local attempts

  for attempts in $(seq 1 60); do
    if ip netns exec "$NS1" batctl meshif bat0 neighbors 2>/dev/null |
      awk '$1 == "eth0" {found = 1} END {exit !found}'; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

start_capture() {
  local pcap="$1"
  local filter="$2"

  tshark -Q -i "$HOST1" -a duration:2 -f "$filter" -w "$pcap" \
    > /dev/null 2> "$pcap.stderr" &
  CAPTURE_PID=$!
  sleep 0.5
}

finish_capture() {
  local capture_pid="$CAPTURE_PID"
  CAPTURE_PID=""
  if ! wait "$capture_pid"; then
    cat "$1.stderr" >&2 || true
    die "tshark capture failed"
  fi
}

assert_broadcast_count() {
  local pcap="$1"
  local expected="$2"
  local rows count unique

  rows="$(tshark -r "$pcap" \
    -Y 'batadv.bcast.seq && udp.dstport == 45678' \
    -T fields -e batadv.bcast.seq -e frame.time_relative 2>/dev/null)" || {
    die "could not decode broadcast capture: $pcap"
  }
  count="$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d' | wc -l)"
  unique="$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d' |
    cut -f1 | sort -u | wc -l)"
  [[ "$count" -eq "$expected" && "$unique" -eq 1 ]] || {
    printf '%s\n' "$rows" >&2
    die "expected $expected copies of one BATADV_BCAST, observed $count copies/$unique sequences"
  }

  echo "[OK] BATADV_BCAST copies: $count (expected $expected)"
  echo "$rows" | sed 's/^/       sequence,time: /'
}

assert_unicast_count() {
  local pcap="$1"
  local rows count

  rows="$(tshark -r "$pcap" \
    -Y 'udp.dstport == 45679 && (batadv.unicast.dst || batadv.unicast_4addr.dst)' \
    -T fields -e frame.number -e frame.time_relative 2>/dev/null)" || {
    die "could not decode unicast capture: $pcap"
  }
  count="$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d' | wc -l)"
  [[ "$count" -eq 1 ]] || {
    printf '%s\n' "$rows" >&2
    die "expected one BATADV_UNICAST, observed $count"
  }
  echo "[OK] BATADV_UNICAST copies: 1"
}

run_case() {
  local mode="$1"
  local expected_bcasts="$2"
  local subnet="$3"
  local hardif_mac bcast_pcap unicast_pcap

  echo "[INFO] Testing $mode"
  "$CONTROL" ensure "$mode"
  batctl routing_algo BATMAN_V
  setup_network "$subnet"
  wait_for_neighbor || die "BATMAN neighbor did not appear within 15 seconds"

  hardif_mac="$(ip netns exec "$NS1" \
    cat /sys/class/net/eth0/address)"
  bcast_pcap="$TMP_DIR/$mode-broadcast.pcap"
  start_capture "$bcast_pcap" \
    "ether proto 0x4305 and ether src $hardif_mac and ether[14] = 1"
  printf 'macewifi-broadcast-smoke' |
    ip netns exec "$NS1" nc -4 -n -u -b -q 0 \
      "10.254.$subnet.255" 45678
  finish_capture "$bcast_pcap"
  assert_broadcast_count "$bcast_pcap" "$expected_bcasts"

  ip netns exec "$NS1" ping -q -c 1 -W 3 "10.254.$subnet.2" >/dev/null || {
    die "unicast reachability check failed"
  }
  unicast_pcap="$TMP_DIR/$mode-unicast.pcap"
  start_capture "$unicast_pcap" \
    "ether proto 0x4305 and ether src $hardif_mac and ether[14] >= 0x40 and ether[14] <= 0x7f"
  printf 'macewifi-unicast-smoke' |
    ip netns exec "$NS1" nc -4 -n -u -q 0 "10.254.$subnet.2" 45679
  finish_capture "$unicast_pcap"
  assert_unicast_count "$unicast_pcap"

  teardown_network || die "could not remove smoke-test network resources"
}

case "$MODE" in
  native)
    run_case native 1 1
    ;;
  emulated_wifi)
    run_case emulated_wifi 3 2
    ;;
  both)
    run_case native 1 1
    run_case emulated_wifi 3 2
    ;;
esac

echo "[OK] batman-adv smoke test completed"
