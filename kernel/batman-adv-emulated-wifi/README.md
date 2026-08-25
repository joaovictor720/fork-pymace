# batman-adv emulated WiFi behavior for MACE/CORE

This directory provides a reproducible, opt-in modification of batman-adv
2019.4 for the `5.4.139-batmanv` MACE VM. It makes non-wireless hard
interfaces such as CORE VETHs use batman-adv's generic WiFi policies. It does
not turn the VETH into an IEEE 802.11 interface and does not replace CORE's
medium model.

The system module under `/lib/modules` is never overwritten. The experimental
module is built inside this repository and loaded by its exact path. The exact
source identity, patch, build procedure and experiment metadata can therefore
be versioned, while the generated binary stays ignored by Git.

## Semantic change

Loading the patched module with `emulated_wifi=1` adds an internal
`BATADV_HARDIF_WIFI_EMULATED` flag only when normal WEXT/cfg80211 detection did
not already identify real WiFi. Calls to `batadv_is_wifi_hardif()` then activate
the policies already present upstream:

- `BATADV_BCAST` is transmitted three times instead of once, with a nominal
  5 ms delay in the source. This VM uses `CONFIG_HZ=250`, so the delay rounds
  up to two jiffies and is expected to appear as approximately 8 ms;
- BATMAN V treats the link as half-duplex;
- BATMAN V may emit its two WiFi ELP probe frames after an idle unicast period;
- translation-table clients learned through the interface receive the WiFi
  marker;
- the BATMAN IV same-interface hop penalty is enabled, although the current
  MACE flow explicitly selects BATMAN V.

The patch deliberately does **not** call cfg80211 for a VETH: there are no real
station statistics to query. BATMAN V keeps the VM's existing Ethernet
ethtool/fallback throughput path, while preventing an ethtool full-duplex value
from undoing the emulated half-duplex policy.

Unchanged behavior includes CORE's bandwidth, range, delay, jitter and loss
configuration, plus the absence of an 802.11 MAC/PHY, contention, rate control,
interference and channel state. This mode isolates batman-adv's response to the
hard-interface classification; it is not a replacement for EMANE or
`mac80211_hwsim` plus `wmediumd`.

## Build

Requirements are Git, GCC, make, kmod tools, and the build headers plus
`Module.symvers` for the running kernel. Build with:

```bash
kernel/batman-adv-emulated-wifi/build.sh
```

The script pins upstream tag `v2019.4` and commit
`933568baeba83d6bcaa451656ec1550346f35996`, verifies the patch, derives all
batman-adv feature switches from the running kernel configuration (including
Network Coding), and writes:

```text
kernel/batman-adv-emulated-wifi/build/$(uname -r)/batman-adv.ko
kernel/batman-adv-emulated-wifi/build/$(uname -r)/batman-adv.ko.sha256
kernel/batman-adv-emulated-wifi/build/$(uname -r)/build-info.txt
```

The build directory is intentionally ignored by Git.

## Select a mode safely

The controller changes only the globally loaded module. It never deletes a
mesh interface, kills a process, force-unloads a module, copies files into
`/lib/modules`, or runs `depmod`.

```bash
# Read-only
kernel/batman-adv-emulated-wifi/module-control.sh status
kernel/batman-adv-emulated-wifi/module-control.sh verify

# Load the repository artifact with emulated WiFi policies
sudo kernel/batman-adv-emulated-wifi/module-control.sh ensure emulated_wifi

# Restore the exact installed native module
sudo kernel/batman-adv-emulated-wifi/module-control.sh rollback
```

Mode changes are refused while batman-adv is in use or when the loaded module
cannot be identified. The setting is global to this kernel: all non-WiFi hard
interfaces added while `emulated_wifi=1` is active receive the emulated flag.
Changing it requires stopping MACE/CORE and reloading the module.

## MACE experiment integration

`evaluation/run_scenario.sh` performs the host preflight before CORE creates
its namespaces. Add the opt-in field to a BATMAN scenario:

```json
"network": {
  "routing": "batman",
  "hardif_behavior": "emulated_wifi",
  "bandwidth": "11000000",
  "range": 160,
  "delay": 5,
  "jitter": 0,
  "error": 0
}
```

Allowed values are `native` and `emulated_wifi`. An absent field means
`native`, so old scenarios retain their previous behavior. Each BATMAN run
records `batman_module.json` in its result directory with the requested and
effective mode, kernel, versions, hashes, upstream commit and repository
commit. Run experiments through `evaluation/run_scenario.sh` (or the wrappers
that call it); invoking `pymace.py` directly bypasses this preflight.

## Acceptance test

With MACE/CORE stopped, run:

```bash
sudo kernel/batman-adv-emulated-wifi/smoke-test.sh both
```

The test creates two temporary namespaces connected by VETHs, captures on the
host side of the transmitting VETH, and checks that one application broadcast
produces one `BATADV_BCAST` in native mode and three copies of one sequence in
emulated-WiFi mode. It also checks that one unicast still produces one BATMAN
unicast frame. Namespaces, bridge and temporary captures are removed on exit,
and the module state present before the test is restored.

For a one-command end-to-end smoke test through MACE/CORE, run:

```bash
./evaluation/run_batman_wifi_smoke.sh
```

This builds the artifact only when necessary, runs a fixed two-node scenario
with the `broadcast` application, and validates module metadata, both
application exit codes, captured BATMAN traffic and at least one broadcast
sequence repeated exactly three times. Results are written under
`results/batman_wifi_smoke/broadcast/<timestamp>/`.

## Reproduce on Andre's VM

The preferred transfer is the Git delta: commit this directory and the
`run_scenario.sh` integration, then run `build.sh` on Andre's VM. This avoids
assuming that two VMs with similar contents have identical kernel symbol
versions.

Copying the `.ko` is acceptable only when architecture, `uname -r`, kernel
configuration and `Module.symvers` are identical. In that case copy the `.ko`,
its `.sha256` file and `build-info.txt` to the same `build/$(uname -r)` path,
then let `module-control.sh` validate version, checksum and `vermagic` before
loading it.

The experimental module is unsigned. This VM permits unsigned modules, which
will taint the kernel. A kernel configured to enforce module signatures will
reject it unless it is signed with a trusted key.
