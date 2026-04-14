# Explanation of this section

The folder Pymace is now a local git repository.It was only to track my changes

Root and user paswword is set to  `mace`

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

#### Run Status And Aggregation

The evaluation harness now writes a `run_status.json` file inside every run directory, for example `results/<variant>/<app>/run_001/run_status.json`.

`run_status.json` records:
- scenario, app, run id, cwd and timestamps
- the exact `pymace.py` command, its return code and the captured logs
- artifact counts such as `node_*.log`, `node_*.net.log`, `node_*.pcap`
- post-processing return codes for `collect_logs.py` and `process_pcaps.py`
- the final `status` and whether the run is `analyzable`

Final `status` values are:
- `setup_failed`: scenario generation, app resolution, node config generation or path injection failed before execution
- `execution_failed_no_artifacts`: execution finished without enough material to analyze the run
- `execution_nonzero_but_artifacts_present`: `pymace.py` returned non-zero but the run still produced enough evidence to analyze it
- `postprocessing_failed`: execution produced artifacts, but log collection or PCAP processing failed in a way that left the run non-analyzable
- `success`: execution and post-processing completed with analyzable outputs

`analyzable=true` is the gate for downstream aggregation. In this first layer, the harness requires enough node logs for parsing plus usable network material from `pcap_metrics.csv`, useful netlogs or useful pcaps.

`--keep-going` changes exit behavior:
- in `run_scenario.sh`, the script still writes the real `status`, but exits `0` even when the run is not analyzable
- in `run_experiment.sh`, the loop continues across non-analyzable runs instead of aborting at the first one

The harness also writes `variant_status.json` in `results/<variant>/<app>/`. Automatic `summary.csv` generation now happens only when every expected run for that variant/app has `analyzable=true`. A run no longer needs `status=success` to be included in aggregation.

Captured runner logs are stored directly in each run directory:
- `pymace.stdout.log`
- `pymace.stderr.log`

