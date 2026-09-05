import json
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

    def run_mace_config(self, value, expect_success=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        (directory / "scenario.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
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
        )
        if expect_success:
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(
                (directory / "mace.json").read_text(encoding="utf-8")
            )
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
        generated_mace = self.run_mace_config(value)

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
        mace = self.run_mace_config(scenario(VALID_GRID))
        self.assertEqual(
            mace["settings"]["experiment_clock_file"],
            "__EXPERIMENT_CLOCK__",
        )
        command = mace["nodes"][0]["function"][0]
        self.assertIn("wait_for_experiment_clock.py", command)
        self.assertIn("timeout --signal=TERM", command)
        self.assertNotIn("sleep 30;", command)


if __name__ == "__main__":
    unittest.main()
