import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
EVALUATION_DIR = ROOT_DIR / "evaluation"
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from parse_metrics import parse_application  # noqa: E402
from plot_spatial_coverage import choose_x, main as plot_main  # noqa: E402
from spatial_results import (  # noqa: E402
    aggregate_car,
    aggregate_tcover,
    parse_spatial_run,
    write_results,
)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _analysis(first_car=0.5, final_car=1.0, t_cover=10.0):
    return {
        "trace_validation": {
            "valid": True,
            "node_count": 2,
            "sample_count": 20,
            "covered_cell_count": 4,
            "total_cell_count": 4,
            "coverage_ratio": 1.0,
            "t_cover_s": t_cover,
            "issues": [],
        },
        "car": {
            "valid": True,
            "t_cover_s": t_cover,
            "checkpoint_offsets_s": [0, 2.5],
            "samples": [
                {
                    "time_s": t_cover,
                    "ground_truth_size": 4,
                    "per_node_replica_size": {"0": 1, "1": 3},
                    "per_node_car": {"0": 0.25, "1": 0.75},
                    "swarm_car": first_car,
                },
                {
                    "time_s": t_cover + 2.5,
                    "ground_truth_size": 4,
                    "per_node_replica_size": {"0": 4, "1": 4},
                    "per_node_car": {"0": final_car, "1": final_car},
                    "swarm_car": final_car,
                },
            ],
            "issues": [],
        },
    }


def _make_run(root, scenario_name, algorithm, run_name, routing="none", seed=1, trace_hash="trace-a", first_car=0.5):
    run_dir = root / scenario_name / algorithm / run_name
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "scenario.json",
        {
            "name": scenario_name,
            "seed": seed,
            "simulation": {"area": {"x": 1000, "y": 1000}},
            "nodes": {"count": 10, "seed": seed},
            "mobility": {"model": "trace", "speed": [4, 4.2]},
            "network": {"routing": routing, "range": 160, "error": 0},
            "grid": {"rows": 2, "cols": 2},
            "node_config": {"dissemination_interval": 1},
        },
    )
    _write_json(
        run_dir / "node_config.json",
        {
            "workload": "spatial_coverage",
            "central_seed": seed,
            "position_poll_interval_ms": 100,
            "dissemination_interval": 1,
            "max_datagram_bytes": 1200,
        },
    )
    _write_json(
        run_dir / "mobility_traces" / "manifest.json",
        {"central_seed": seed, "combined_sha256": trace_hash, "model": "trace", "nodes": [{}, {}]},
    )
    _write_json(run_dir / "spatial_coverage_analysis.json", _analysis(first_car=first_car))
    return run_dir


class SpatialRunParsingTests(unittest.TestCase):
    def test_summary_and_long_tables_include_arbitrary_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "results"
            run_dir = _make_run(root, "coverage_ip", "rapid", "run_1")
            summary, checkpoints, nodes = parse_spatial_run(run_dir, root)

        self.assertEqual(summary["workload"], "spatial_coverage")
        self.assertTrue(summary["analysis_valid"])
        self.assertEqual(summary["car_swarm_tcover"], 0.5)
        self.assertEqual(summary["car_swarm_tcover_plus_2p5s"], 1.0)
        self.assertEqual(summary["final_swarm_car"], 1.0)
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(checkpoints[0]["node_car_mean"], 0.5)
        self.assertEqual(checkpoints[0]["node_car_min"], 0.25)

    def test_malformed_checkpoint_cardinality_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "results"
            run_dir = _make_run(root, "coverage_ip", "rapid", "run_1")
            value = _analysis()
            value["car"]["checkpoint_offsets_s"].append(5)
            _write_json(run_dir / "spatial_coverage_analysis.json", value)
            with self.assertRaisesRegex(ValueError, "checkpoint offsets"):
                parse_spatial_run(run_dir, root)

    def test_per_application_parser_writes_spatial_summary_not_gcounter_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "rapid"
            run_dir = _make_run(Path(tmp), "unused", "rapid", "run_1")
            # parse_application expects runs immediately below the app dir.
            app_dir.mkdir(exist_ok=True)
            target = app_dir / "run_1"
            run_dir.rename(target)
            self.assertEqual(parse_application(app_dir), 1)
            with (app_dir / "summary.csv").open(newline="", encoding="utf-8") as source:
                row = next(csv.DictReader(source))

        self.assertEqual(row["workload"], "spatial_coverage")
        self.assertEqual(row["car_swarm_tcover_plus_2p5s"], "1.0")
        self.assertNotIn("convergence_time_s", row)


class SpatialAggregationTests(unittest.TestCase):
    def test_car_confidence_interval_uses_runs_not_nodes(self):
        base = {
            "scenario_family": "coverage",
            "datapoint_key": "same",
            "algorithm": "rapid",
            "checkpoint_offset_s": 0.0,
            "checkpoint_label": "tcover",
            "analysis_valid": True,
            "node_car_mean": 0.5,
            "node_car_min": 0.0,
            "ground_truth_size": 4,
        }
        rows = [dict(base, run="run_1", swarm_car=0.25), dict(base, run="run_2", swarm_car=0.75)]
        result = aggregate_car(rows)[0]
        self.assertEqual(result["runs_total"], 2)
        self.assertEqual(result["swarm_car_n"], 2)
        self.assertEqual(result["swarm_car_mean"], 0.5)

    def test_tcover_deduplicates_shared_trace_across_algorithms(self):
        base = {
            "scenario_family": "coverage",
            "datapoint_key": "same",
            "trace_valid": True,
            "trace_hash": "trace-a",
            "t_cover_s": 12.0,
        }
        result = aggregate_tcover([dict(base, algorithm="rapid"), dict(base, algorithm="trickle")])[0]
        self.assertEqual(result["trace_repetitions"], 1)
        self.assertEqual(result["algorithm_observations"], 2)
        self.assertEqual(result["t_cover_s_n"], 1)

    def test_seed_and_routing_do_not_split_a_paired_datapoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "results"
            first = _make_run(root, "coverage_ip", "rapid", "run_1", routing="none", seed=1)
            second = _make_run(root, "coverage_batman", "broadcast", "run_1", routing="batman", seed=9)
            one = parse_spatial_run(first, root)[0]
            two = parse_spatial_run(second, root)[0]

        self.assertEqual(one["scenario_family"], two["scenario_family"])
        self.assertEqual(one["datapoint_key"], two["datapoint_key"])

    def test_batch_writer_creates_all_six_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "results"
            output = Path(tmp) / "tables"
            _make_run(root, "coverage_ip", "rapid", "run_1")
            counts = write_results(root, output, strict=True)
            files = {path.name for path in output.glob("*.csv")}

        self.assertEqual(counts["runs"], 1)
        self.assertEqual(len(files), 6)
        self.assertIn("aggregated_spatial_car.csv", files)

    def test_plot_pipeline_generates_every_spatial_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "results"
            tables = Path(tmp) / "tables"
            plots = Path(tmp) / "plots"
            _make_run(root, "coverage_ip", "rapid", "run_1")
            write_results(root, tables, strict=True)
            result = plot_main(
                [
                    "--input-dir", str(tables),
                    "--output-dir", str(plots),
                    "--formats", "png",
                ]
            )
            names = {path.name for path in plots.glob("*.png")}

        self.assertEqual(result, 0)
        self.assertTrue(any(name.startswith("car_recovery_") for name in names))
        self.assertIn("car_coverage_plus_2.5s.png", names)
        self.assertTrue(any(name.startswith("car_nodes_final_") for name in names))
        self.assertTrue(any(name.startswith("car_overhead_") for name in names))
        self.assertTrue(any(name.startswith("tcover_") for name in names))

    def test_plot_axis_auto_detection_prefers_varying_numeric_parameter(self):
        import pandas as pd

        frame = pd.DataFrame({"nodes_cfg": [10, 20], "grid_cell_count": [4, 4]})
        self.assertEqual(choose_x(frame), "nodes_cfg")


if __name__ == "__main__":
    unittest.main()
