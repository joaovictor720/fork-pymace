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


