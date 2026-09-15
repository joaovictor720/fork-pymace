import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
EVALUATION_DIR = ROOT_DIR / "evaluation"


def scenario(grid=None):
    value = {
        "name": "spatial_test",
        "seed": 7,
        "simulation": {
            "duration": 60,
            "start_delay": 1,
            "area": {"x": 40, "y": 40},
        },
        "nodes": {"count": 1, "distribution": "grid"},
        "mobility": {"model": "none", "pause": 0, "speed": [0, 0]},
        "network": {
            "routing": "none",
            "bandwidth": "11000000",
            "range": 100,
            "delay": 0,
            "jitter": 0,
            "error": 0,
        },
        "node_config": {
            "workload": "spatial_coverage",
            "udp_port": 5001,
            "ops_per_sec": 99,
            "diss_per_sec": 2,
            "duration": 10,
            "cooldown": 20,
            "monitor_interval": 1,
        },
        "coverage": {
            "start_delay_s": 30,
            "post_coverage_window_s": 20,
        },
    }
    if grid is not None:
        value["grid"] = grid
    return value


VALID_GRID = {
    "origin_x_m": 0,
    "origin_y_m": 0,
    "width_m": 40,
    "height_m": 40,
    "rows": 4,
    "cols": 4,
}


class SpatialConfigGenerationTests(unittest.TestCase):
    def run_node_config(self, value, expect_success=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = directory / "scenario.json"
        output = directory / "node_config.json"
        result_dir = directory / "result"
        result_dir.mkdir()
        source.write_text(json.dumps(value), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(EVALUATION_DIR / "generate_node_config.py"),
                str(source),
                str(output),
                str(result_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        if expect_success:
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(output.read_text(encoding="utf-8"))
        self.assertNotEqual(completed.returncode, 0)
        return completed

    def run_mace_config(self, value, expect_success=True, env=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        (directory / "scenario.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        completed = subprocess.run(
            [
                sys.executable,
                str(EVALUATION_DIR / "generate_scenario.py"),
                str(directory),
                "rapid",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            env=run_env,
        )
        if expect_success:
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(
                (directory / "mace.json").read_text(encoding="utf-8")
            ), directory
        self.assertNotEqual(completed.returncode, 0)
        return completed

    def test_spatial_config_copies_grid_but_not_experiment_controls(self):
        generated = self.run_node_config(scenario(VALID_GRID))
        self.assertEqual(generated["workload"], "spatial_coverage")
        self.assertEqual(generated["grid"], VALID_GRID)
        self.assertEqual(generated["position_poll_interval_ms"], 100)
        self.assertEqual(generated["max_datagram_bytes"], 1200)
        self.assertEqual(generated["dissemination_interval"], 0.5)
        for forbidden in (
            "ops_per_sec",
            "duration",
            "cooldown",
            "post_coverage_window_s",
            "checkpoints",
            "diss_per_sec",
        ):
            self.assertNotIn(forbidden, generated)

    def test_grid_must_match_mobility_domain(self):
        bad_grid = dict(VALID_GRID, width_m=39)
        completed = self.run_node_config(
            scenario(bad_grid), expect_success=False
        )
        self.assertIn("coincide", completed.stderr)

    def test_grid_must_fit_strictest_primitive_budget(self):
        too_large = dict(VALID_GRID, rows=25, cols=24)
        completed = self.run_node_config(
            scenario(too_large), expect_success=False
        )
        self.assertIn("budget", completed.stderr)

    def test_grid_rejects_boolean_string_and_nonfinite_numeric_fields(self):
        invalid_fields = (
            ("origin_x_m", False),
            ("width_m", "40"),
            ("height_m", float("nan")),
            ("rows", True),
            ("cols", "4"),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value=value):
                grid = dict(VALID_GRID)
                grid[field] = value
                completed = self.run_node_config(
                    scenario(grid), expect_success=False
                )
                self.assertTrue(completed.stderr.strip())

    def test_spatial_intervals_reject_boolean_string_and_nan(self):
        invalid_values = (
            ("position_poll_interval_ms", "100"),
            ("gps_timeout_ms", True),
            ("max_datagram_bytes", "1200"),
            ("monitor_interval", float("nan")),
            ("dissemination_interval", "0.5"),
            ("diss_per_sec", False),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                value_scenario = scenario(VALID_GRID)
                value_scenario["node_config"][field] = value
                completed = self.run_node_config(
                    value_scenario, expect_success=False
                )
                self.assertTrue(completed.stderr.strip())

    def test_spatial_requires_boolean_true_deterministic_replay(self):
        for invalid in (False, "false", "true", 1):
            with self.subTest(value=invalid):
                value = scenario(VALID_GRID)
                value["mobility"]["deterministic_replay"] = invalid
                completed = self.run_mace_config(value, expect_success=False)
                self.assertIn("deterministic_replay", completed.stderr)

    def test_gcounter_does_not_parse_spatial_coverage_controls(self):
        value = scenario(VALID_GRID)
        value["node_config"]["workload"] = "gcounter"
        value["coverage"] = "not a spatial configuration"

        generated_node = self.run_node_config(value)
        generated_mace, _ = self.run_mace_config(value)

        self.assertEqual(generated_node["workload"], "gcounter")
        self.assertNotIn("experiment_clock_file", generated_mace["settings"])
        self.assertIn("sleep 30;", generated_mace["nodes"][0]["function"][0])

    def test_runner_removes_stale_spatial_clock_before_launch(self):
        runner = (EVALUATION_DIR / "run_scenario.sh").read_text(
            encoding="utf-8"
        )
        removal = 'rm -f -- "$CLOCK_FILE"'
        launch = 'sudo "$ROOT_DIR/pymace.py"'
        self.assertIn(removal, runner)
        self.assertLess(runner.index(removal), runner.index(launch))

    def test_generated_mace_uses_external_clock_and_termination(self):
        mace, _ = self.run_mace_config(scenario(VALID_GRID))
        self.assertEqual(
            mace["settings"]["experiment_clock_file"],
            "__EXPERIMENT_CLOCK__",
        )
        command = mace["nodes"][0]["function"][0]
        self.assertIn("wait_for_experiment_clock.py", command)
        self.assertIn("timeout --signal=TERM", command)
        self.assertNotIn("sleep 30;", command)

    def test_trace_catalog_uses_run_id_and_node_prefix(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        catalog = root / "catalog"
        for run_name, x0 in (("run_001", 1.0), ("run_002", 11.0)):
            run_dir = catalog / run_name
            run_dir.mkdir(parents=True)
            nodes = []
            for node in range(3):
                path = run_dir / "node_{}.csv".format(node)
                path.write_text(
                    "time_s,x_m,y_m,z_m\n"
                    "0.000000000,{:.9f},0.000000000,0.000000000\n"
                    "1.000000000,{:.9f},1.000000000,0.000000000\n".format(
                        x0 + node,
                        x0 + node,
                    ),
                    encoding="utf-8",
                )
                nodes.append({
                    "file": path.name,
                    "node": node,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "rows": 2,
                    "seed": node + 1,
                    "role": "test",
                })
            manifest = {
                "policy": "deterministic_mobility_trace_v1",
                "catalog_policy": "test_catalog",
                "central_seed": 123,
                "model": "test_catalog",
                "interval_s": 1.0,
                "duration_s": 1.0,
                "nodes": nodes,
            }
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

        value = scenario(VALID_GRID)
        value["nodes"]["count"] = 2
        value["mobility"] = {
            "model": "trace_catalog",
            "deterministic_replay": True,
            "trace_catalog": str(catalog),
            "trace_interval": 1.0,
            "pause": 0,
            "speed": [0, 0],
        }

        mace, directory = self.run_mace_config(
            value,
            env={"MACE_RUN_ID": "smoke_001", "MACE_TRACE_SET": "run_002"},
        )
        trace_dir = directory / "mobility_traces"
        materialized = json.loads(
            (trace_dir / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(materialized["catalog_run"], "run_002")
        self.assertEqual(len(materialized["nodes"]), 2)
        self.assertFalse((trace_dir / "node_2.csv").exists())
        self.assertIn("11.000000000", (trace_dir / "node_0.csv").read_text())
        self.assertIn("12.000000000", (trace_dir / "node_1.csv").read_text())
        for node in mace["nodes"]:
            mobility = node["extra"]["mobility"]
            self.assertEqual(mobility["source_model"], "test_catalog")
            self.assertTrue(Path(mobility["trace_file"]).exists())

    def test_runner_passes_run_id_to_scenario_generation(self):
        runner = (EVALUATION_DIR / "run_scenario.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('MACE_RUN_ID="$RUN_ID" python', runner)

    def test_runner_tolerates_legacy_sigkill_after_valid_spatial_analysis(self):
        runner = (EVALUATION_DIR / "run_scenario.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"$PYMACE_RC" -eq 137', runner)
        self.assertIn('"$RESULT_DIR/spatial_coverage_analysis.json"', runner)
        self.assertIn("legacy self-SIGKILL", runner)

    def test_batch_scripts_support_incremental_run_ranges(self):
        run_experiment = (EVALUATION_DIR / "run_experiment.sh").read_text(
            encoding="utf-8"
        )
        run_all = (EVALUATION_DIR / "run_all.sh").read_text(
            encoding="utf-8"
        )
        wrapper = (EVALUATION_DIR / "run_spatial_grid_repro.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("--start-run) START_RUN=", run_experiment)
        self.assertIn("END_RUN=$((START_RUN + RUNS - 1))", run_experiment)
        self.assertIn('for RUN in $(seq "$START_RUN" "$END_RUN"); do', run_experiment)
        self.assertIn('if (( START_RUN > 1 )); then', run_experiment)

        self.assertIn("--start-run) START_RUN=", run_all)
        self.assertIn('--start-run "$START_RUN"', run_all)

        self.assertIn("--start-run)", wrapper)
        self.assertIn("if (( START_RUN == 1 )); then", wrapper)
        self.assertIn("Preserving existing spatial results", wrapper)
        self.assertIn('--start-run "$START_RUN"', wrapper)


if __name__ == "__main__":
    unittest.main()
