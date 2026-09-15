#!/bin/bash
set -euo pipefail

# Run this wrapper with sudo so results land under results/density_ip__expanded
# alongside the published rapid runs.

bash evaluation/run_experiment.sh --scenario density_ip --app trickle_overhead --runs 10
bash evaluation/run_experiment.sh --scenario density_ip --app trickle_latency --runs 10
