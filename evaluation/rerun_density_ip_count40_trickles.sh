#!/bin/bash
set -euo pipefail

# Run this wrapper with sudo to replace only the count=40 results for the two
# trickle profiles under results/density_ip__expanded/.

bash evaluation/run_experiment.sh --scenario density_ip --app trickle_overhead --runs 10 --variants count=40
bash evaluation/run_experiment.sh --scenario density_ip --app trickle_latency --runs 10 --variants count=40
