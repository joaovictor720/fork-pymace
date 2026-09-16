# Explanation of this section

The folder Pymace is now a local git repository.It was only to track my changes

Root and user paswword is set to  `mace`

#### Reproducibility seed policy

Experiment generation uses one central deterministic seed. Prefer a top-level
`seed` field in `scenario.json`; when it is absent, the legacy `nodes.seed`
field is used as the central seed.

The generated files derive independent deterministic streams from that central
seed:
- `node_positions`: initial node placement in `mace.json`.
- `mobility`: per-node mobility model seeds in `mace.json`.
- `application`: CRDT application PRNG seed in `node_config.json`.

For stochastic mobility models, `generate_scenario.py` now precomputes a
deterministic mobility trace from the per-node mobility seed by default. The
trace files are stored under `mobility_traces/`, referenced by absolute path in
`mace.json`, and validated at runtime with SHA-256 before replay. The original
mobility model name is kept in `mace.json`; `deterministic_replay=true` means
the online mobility thread replays the generated trace instead of drawing new
random samples during the emulation.

`run_scenario.sh` stores the executed `scenario.json`, generated `mace.json`,
generated `node_config.json`, and copied `mobility_traces/` in each run result
directory. These files are the reproducibility manifest for a run.

#### Spatial coverage workload

All seven CRDT applications (`broadcast`, `multiunicast`, `rapid`, `trickle`,
`usfd`, `usfdx1`, and `usfdx3`) accept `workload: "spatial_coverage"`. A
minimal scenario fragment is:

```json
{
  "simulation": {
    "duration": 120,
    "area": {"x": 160, "y": 160}
  },
  "grid": {
    "origin_x_m": 0,
    "origin_y_m": 0,
    "width_m": 160,
    "height_m": 160,
    "rows": 16,
    "cols": 16
  },
  "node_config": {
    "workload": "spatial_coverage",
    "position_poll_interval_ms": 100,
    "gps_timeout_ms": 50,
    "max_datagram_bytes": 1400,
    "dissemination_interval": 0.5,
    "monitor_interval": 1
  },
  "coverage": {
    "start_delay_s": 30,
    "post_coverage_window_s": 20,
    "checkpoints": [0, 5, 10, 15, 20]
  }
}
```

The grid must exactly match `simulation.area`. The generator validates the
full bitmap against the strictest primitive header (13 bytes for RAPID/Trickle).
The coverage frame has a 44-byte header: `CBM1`, rows and columns as big-endian
uint32, followed by origin x/y and width/height as big-endian IEEE-754 binary64.
The remaining `ceil(rows * cols / 8)` bytes are the bitmap: cell 0 is the low
bit of byte 0, cell 7 is its high bit, and cell 8 is the low bit of byte 1.
Unused final bits must be zero. Receivers reject differing grids, malformed
lengths and nonzero padding. This encoding is incompatible with old spatial
ID-list payloads; all nodes in a run must use the new binaries. GCounter is
unchanged.

The proposed default budget is 1400 bytes of UDP payload, including application
and primitive headers. Maximum cells are
`min(65536, 8 * (budget - 44 - primitive_header_bytes))`: 10,848 for broadcast
and multi-unicast (0), 10,784 for USFD (8), and 10,744 for RAPID/Trickle (13).
The shared generator uses the strictest limit; 103x103 is the largest square
that fits all primitives. An explicit 1200-byte budget remains supported
(9,144 cells with the strictest header). Oversized grids fail before startup;
there is no fragmentation, compression or encoding fallback in the application.

**BATMAN-adv path validation remains pending:** during bitmap implementation,
the host interface reported MTU 1500, but no CORE/BATMAN interfaces or loaded
`batman_adv` module were available to measure the mesh path. Do not treat
1400 as a verified mesh limit: inspect `eth0` and `bat0` MTUs in the running
CORE nodes, then probe a 1428-byte IPv4 packet (1400 UDP payload + 8 UDP +
20 IP) and check physical-interface captures for IP/BATMAN fragmentation.
The UDP budget check alone cannot establish that the mesh avoids fragmentation.

Duration, cooldown, checkpoints, and `T_cover` are experiment-runner concerns and are not written into the
application configuration.

Before starting CORE, `run_scenario.sh` checks that there is one deterministic
trace per node, that the traces cover the complete grid after the configured
coverage epoch, and that they extend through the post-coverage window. It then
derives the external stop time from `T_cover`, writes
`trace_validation.json` and `experiment_plan.json`, and publishes a shared
`experiment_clock.json`. After the run it writes
`spatial_coverage_analysis.json`, containing per-node and swarm CAR at the
configured checkpoints. When explicitly configured,
`mobility.deterministic_replay` must be the JSON boolean `true`; quoted boolean
strings are rejected.

The checker/analyzer can also be run directly:

```bash
python3 evaluation/spatial_coverage.py check-traces \
  --grid-config node_config.json \
  --coverage-start-time-s 30 \
  --post-coverage-window-s 20 \
  mobility_traces/node_*.csv

python3 evaluation/spatial_coverage.py analyze \
  --run-dir results/my_scenario/rapid/1 \
  --experiment-clock auto
```

To collect every completed spatial run, calculate mean, sample standard
deviation, and Student-t 95% confidence intervals, and generate the old-style
convergence and network-cost plots:

```bash
./evaluation/gera_spatial.sh
```

An alternate results directory and plot directory can be passed as the first
and second arguments. The script discovers
`spatial_coverage_analysis.json` recursively, so it does not depend on a
particular scenario name, trace distribution, sweep parameter, or jobs file.
It writes:

- `all_spatial_runs.csv`: one row per run, including final CAR and network cost;
- `all_spatial_car_checkpoints.csv`: one row per run/checkpoint;
- `all_spatial_car_nodes.csv`: descriptive per-node observations;
- `aggregated_spatial_car.csv`: CAR statistics by algorithm and checkpoint;
- `aggregated_spatial_tcover.csv`: physical coverage-time statistics;
- `aggregated_spatial_overhead.csv`: final CAR and network-cost statistics;
- `all_spatial_usage_checkpoints.csv`: per-run network cost accumulated up to
  each CAR checkpoint; and
- `aggregated_spatial_usage_checkpoints.csv`: checkpoint network-cost
  statistics by algorithm and scenario parameter.

The run, rather than each node, is the independent unit in CAR confidence
intervals. `T_cover` is deduplicated by mobility-trace hash across algorithms,
because replaying one trace with multiple dissemination algorithms does not
create additional independent mobility observations. The generated plots live
under `results/plots/spatial/`: `__convergence.pdf`,
`__convergence_zoom_y.pdf`, and `__usage.pdf`. Each PDF has one page per
configured checkpoint offset. When packet loss varies outside the selected
x-axis, the plotter writes separate `__loss_0pct__*` and `__loss_10pct__*`
PDFs instead of overlaying both loss levels on the same density points. Use
`--x COLUMN` with `plot_spatial_coverage.py` to override automatic sweep-axis
detection.

Spatial event logs distinguish `local_coverage`, `remote_merge`,
`dissemination_trigger`, received traffic, and primitive publish/reset actions.
The monitor logs separately retain cumulative `sent_msgs`, `recv_msgs`,
`sent_bytes`, and `recv_bytes` for actual network traffic. Event timestamps use
Unix time and are aligned to trace-relative time through
`experiment_clock.json`; `replica_version` orders concurrent state mutations.

#### Spatial grid density experiment

`scenarios/spatial_grid_ip` and `scenarios/spatial_grid_batman` define the
1km x 1km spatial workload used for density and packet-loss comparisons.  Both
scenarios use a 24x24 `GridSpec` (576 cells), `coverage.start_delay_s=30`, and
`coverage.post_coverage_window_s=10`.  The density sweep is cumulative:
10, 20, 30, 40 and 50 nodes use prefixes of the same run trace set. Each
density is evaluated with configured packet error rates of 0% and 10%. The
radio range is fixed at 140m and the deterministic mobility traces are
generated with a fixed 20m/s movement speed.

The fixed mobility inputs live in
`evaluation/trace_catalogs/spatial_grid_1km_24x24`.  The catalog contains 10
independent trace sets with 50 node CSVs each.  In every set, nodes 0..9 receive
balanced compact coverage sectors, so the initial coverage work is split as
evenly as possible across the 10 coverage drones without assigning scattered
far-apart cells that would create unrealistic transit jumps.  The sector
geometry varies across runs using horizontal, vertical, and oblique sweep
families; each sector is then routed with a short local sweep and within-cell
jitter.  This guarantees physical coverage of the full grid after the 30s
coverage-start offset; nodes 10..49 are seeded random patrols that increase
density and connectivity variation.  `run_scenario.sh` passes its `RUN_ID` to
`generate_scenario.py`, which materializes only the first N traces needed by
the expanded density variant.

Regenerate the catalog only when intentionally changing the mobility design:

```bash
python3 evaluation/generate_spatial_trace_catalog.py --force
```

Run the spatial comparison with 10 runs per density/app:

```bash
./evaluation/run_spatial_grid_repro.sh 10
```

To split the same 10-run experiment into two batches while preserving the
first batch:

```bash
./evaluation/run_spatial_grid_repro.sh 5
./evaluation/run_spatial_grid_repro.sh --runs 5 --start-run 6
```

Build and run the C++ unit tests with:

```bash
apps/crdt/build.sh test
```

#### Main loop script
` do_it.sh  ` is an executable that will automate a bit the execution of the scenarios.
To use it you may want to change the values of :
- CONFIG_FILE: Path to a JSON configuration file.
- CONCURRENCY_VALUES: Array of different concurrency values to test.
- ITERATIONS: Number of iterations for each concurrency level.
- sudo ./pymace.py -s ./scenarios/yourJsonFile.json : set `yourJsonFile` with you scenario file.

Main Loop:
-    Iterates through each value of CONCURRENCY_VALUES.
-    For each value, it modifies the concurrency setting in the JSON config file.
-    It runs a test (pymace.py) multiple times (specified by ITERATIONS), executes it, waits for completion, and processes any output files.
-    The output files (client*) are copied, processed with a Python script `get.py` who will read the output of client log file in `temp/node0/`, and then cleaned up after each iteration.

In `get.py` you may want to change the `output_file` at the end of the code. The interesting values will be saved in a csv file.

Note that the `do_it.sh` will erase the nodes log file.

I often run `cd && time ./Documents/pymace/evaluation/do_it.sh` to run and have a track of the execution time.

#### Plot the results
To use the results from the csv files :
- `get_means.py` to plot averaged values of median latency, throughput grouped and sorted by value of concurency.
- `get_fail.py` to get the percentage of execution fails, each empty line in a csv = 1 fail so do not delete the empty lines.
- `get_error.py` will evaluate the standard deviation modify lines 69,70,79,80 to disable/enable std dev.
- `plot.py` will plot each value discriminated by concurency.

Except for `plot.py`, all files in the `files_with_labels.py` list will be on the same figure. You can change the color label and linestyle with argument in this list.
