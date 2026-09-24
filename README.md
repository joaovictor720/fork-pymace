# pymace - Python Mobile Ad-Hoc Computing Emulator
This software is part of the paper: MACE: Mobile Ad-Hoc Computing Emulator

## Installation

For Ubuntu 24.04 / Python 3.12, use the [online setup guide](setup/README.md).
The setup downloads pinned dependencies, builds CORE 9.2.0 locally, and optionally
builds BATMAN V for the running kernel. Run as your normal user:

```bash
./setup/setup.sh --batman emulated_wifi # research profile; apt uses sudo
./setup/setup.sh                       # basic MACE without BATMAN
./setup/doctor.sh --mode emulated_wifi  # read-only checks
```

`--batman native` prepares upstream BATMAN V without the Wi-Fi emulation patch.
`--check` previews the plan; `--skip-system` uses preinstalled system packages.
`./install.sh` delegates to this setup. Sources and build products stay outside Git.
Secure Boot signing/enrollment and module loading are explicit steps described in
the guide. Repeating setup preserves a valid module's signature. No DKMS or offline
bundle is included. Compilation/unit tests and the two-node broadcast smoke test
with emulated Wi-Fi have passed on physical Ubuntu with Secure Boot enabled.
Larger experiments and VM comparisons remain pending (see the [portability report](docs/portabilidade-ubuntu.md)).

The remainder describes the historical VM setup and optional emulator integrations.

### Network emulators

*CORE*

CORE can be downloaded here: https://github.com/coreemu/core/releases
This was tested with release 7.2.1 and instructions on how to install can be found here: https://coreemu.github.io/core/install.html

The historical VM used a global CORE installation. New installations should use
the repository-local setup above.

*OMNet++*

OMNet++ Installation guide can be found here:
https://omnetpp.org/doc/omnetpp/InstallGuide.pdf

This work has been tested with OMNet++ 5.6.2

### Linux

Some packages need to be installed on Linux:

- batctl: Used for configuring the B.A.T.M.A.N routing protocol.
- xterm: Used when opening terminals inside the namespaces. Only reason is because it is more compact to open several simultaneously.


### Python

The following Python packages are required:

flask
flask_socketio==4.3.2
apscheduler
ping3
geopy
pymobility

Optional Python packages:

- pprzlink: only required for PAPARAZZI-based mobility or PPRZ network integrations. Regular socket-based evaluation scenarios do not require it.

This work has been testes with Python 3.8

### PyMACE

Historical deployments placed pymace under `/opt`. The native setup above runs
from the checkout and does not need that location.
