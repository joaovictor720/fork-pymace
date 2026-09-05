import contextlib
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
EVALUATION_DIR = ROOT_DIR / "evaluation"
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from spatial_coverage import (  # noqa: E402
    CELL_ID_CAPACITY,
    CarAnalysis,
    GridSpec,
    GroundTruthTimeline,
    Position,
    ReplicaEvent,
    ReplicaTimeline,
    TimeAlignmentError,
    TraceFormatError,
    TraceSample,
    TraceValidationError,
    analyze_car,
    build_ground_truth,
    build_replica_timelines,
    check_traces,
    compute_car_samples,
    main,
    read_event_log,
    read_experiment_clock,
    read_trace,
    validate_trace,
)


def sample(node, time_s, x, y, z=0.0):
    return TraceSample(float(time_s), Position(float(x), float(y), float(z)), str(node))


class GridSpecTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridSpec(0.0, 0.0, 40.0, 40.0, 4, 4)

    def test_mapping_and_inverse_match_cpp_contract(self):
        self.assertEqual(self.grid.locate((0.0, 0.0)).id, 0)
        self.assertEqual(self.grid.locate((10.0, 10.0)).id, 5)
        self.assertEqual(self.grid.locate((39.0, 39.0)).id, 15)

        for cell_id in range(self.grid.cell_count):
            cell = self.grid.from_id(cell_id)
            self.assertEqual(cell.id, cell_id)
            self.assertEqual(cell.row * self.grid.cols + cell.col, cell_id)
            self.assertEqual(self.grid.row_col(cell_id), (cell.row, cell.col))

    def test_boundaries_use_floor_and_tolerated_upper_clamp(self):
        epsilon = self.grid.tolerance()
        self.assertEqual(self.grid.locate((40.0, 40.0)).id, 15)
        self.assertEqual(
            self.grid.locate((40.0 + epsilon * 0.5, 20.0)).id,
            11,
        )
        self.assertEqual(self.grid.locate((-epsilon * 0.5, 0.0)).id, 0)
        self.assertIsNone(self.grid.locate((40.0 + epsilon * 2.0, 20.0)))
        self.assertIsNone(self.grid.locate((-1.0, 0.0)))
        self.assertIsNone(self.grid.locate((float("nan"), 0.0)))
        self.assertIsNone(self.grid.locate((0.0, 0.0, float("inf"))))

        negative_origin = GridSpec(-40.0, -40.0, 40.0, 40.0, 4, 4)
        self.assertEqual(negative_origin.locate((0.0, 0.0)).id, 15)

    def test_invalid_grid_and_cell_ids_are_rejected(self):
        bad_specs = (
            (0, 0, 0, 10, 1, 1),
            (0, 0, 10, -1, 1, 1),
            (0, 0, 10, 10, 0, 1),
            (0, 0, 10, 10, 1, True),
            (0, 0, 10, 10, 257, 256),
            (True, 0, 10, 10, 1, 1),
            (0, 0, "10", 10, 1, 1),
            (0, 0, 10, 10, "1", 1),
            (0, 0, 10, 10, 10 ** 400, 1),
            (float("nan"), 0, 10, 10, 1, 1),
            (1e308, 0, 1, 10, 1, 1),
            (1e308, 0, 1e308, 10, 1, 1),
            (0, 0, float.fromhex("0x0.0000000000001p-1022"), 10, 1, 2),
        )
        for values in bad_specs:
            with self.subTest(values=values), self.assertRaises(ValueError):
                GridSpec(*values)
        self.assertEqual(CELL_ID_CAPACITY, 65536)
        with self.assertRaises(ValueError):
            self.grid.from_id(-1)
        with self.assertRaises(ValueError):
            self.grid.from_id(16)

    def test_horizontal_vertical_and_corner_traversal(self):
        self.assertEqual(
            self.grid.traverse((1, 1), (39, 1)),
            [0, 1, 2, 3],
        )
        self.assertEqual(
            self.grid.traverse((1, 1), (1, 39)),
            [0, 4, 8, 12],
        )
        self.assertEqual(
            self.grid.traverse((1, 1), (39, 39)),
            [0, 5, 10, 15],
        )
        self.assertEqual(
            self.grid.traverse((39, 39), (1, 1)),
            [15, 10, 5, 0],
        )

    def test_non_square_diagonal_and_internal_boundary_match_cpp_vectors(self):
        self.assertEqual(
            self.grid.traverse((1, 1), (19, 39)),
            [0, 4, 9, 13],
        )
        self.assertEqual(
            self.grid.traverse((10, 1), (10, 39)),
            [1, 5, 9, 13],
        )
        # A non-zero dx whose endpoints remain in the same column must not
        # advance x after that axis is already at its target.
        self.assertEqual(
            self.grid.traverse((1, 1), (9, 39)),
            [0, 4, 8, 12],
        )

    def test_traversal_includes_endpoints_once_and_rejects_invalid_endpoint(self):
        self.assertEqual(self.grid.traverse((1, 1), (2, 2)), [0])
        crossed = self.grid.traverse((1, 1), (39, 20))
        self.assertEqual(len(crossed), len(set(crossed)))
        self.assertEqual(crossed[0], 0)
        self.assertEqual(crossed[-1], self.grid.locate((39, 20)).id)
        self.assertEqual(self.grid.traverse((-1, 1), (20, 1)), [])


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridSpec(0, 0, 2, 2, 2, 2)

    def complete_traces(self):
        return {
            "0": [
                sample("0", 0, 0.1, 0.1),
                sample("0", 1, 1.1, 0.1),
                sample("0", 2, 1.1, 1.1),
                sample("0", 22, 1.1, 1.1),
            ],
            "1": [
                sample("1", 0, 0.1, 1.1),
                sample("1", 1, 0.1, 1.1),
                sample("1", 2, 0.1, 1.1),
                sample("1", 22, 0.1, 1.1),
            ],
        }

    def test_read_trace_csv_and_infer_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_12.csv"
            path.write_text(
                "time_s,x_m,y_m,z_m\n0,0.1,0.2,3\n1,1.1,0.2,4\n",
                encoding="utf-8",
            )
            rows = read_trace(path)
        self.assertEqual([row.node for row in rows], ["12", "12"])
        self.assertEqual(rows[0].position, Position(0.1, 0.2, 3.0))

    def test_read_trace_rejects_bad_header_and_non_finite_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.csv"
            missing.write_text("time_s,x_m\n0,1\n", encoding="utf-8")
            with self.assertRaises(TraceFormatError):
                read_trace(missing)

            nonfinite = Path(tmp) / "nonfinite.csv"
            nonfinite.write_text(
                "time_s,x_m,y_m\n0,nan,1\n", encoding="utf-8"
            )
            with self.assertRaises(TraceFormatError):
                read_trace(nonfinite)

            empty_column = Path(tmp) / "empty_column.csv"
            empty_column.write_text(
                "time_s,x_m,y_m,\n0,1,1,ignored\n", encoding="utf-8"
            )
            with self.assertRaises(TraceFormatError):
                read_trace(empty_column)

            malformed_quote = Path(tmp) / "malformed_quote.csv"
            malformed_quote.write_text(
                'time_s,x_m,"y_m\n0,1,1\n', encoding="utf-8"
            )
            with self.assertRaises(TraceFormatError):
                read_trace(malformed_quote)

    def test_validation_rejects_nonmonotonic_and_out_of_bounds(self):
        rows = [
            sample("0", 0, 0.1, 0.1),
            sample("0", 0, 0.2, 0.2),
            sample("0", 1, 20, 0.2),
        ]
        with self.assertRaises(TraceValidationError) as caught:
            validate_trace(rows, self.grid, require_start_at_zero=True)
        codes = {issue.code for issue in caught.exception.issues}
        self.assertIn("non_monotonic_time", codes)
        self.assertIn("position_out_of_bounds", codes)

    def test_ground_truth_groups_nodes_at_equal_timestamp(self):
        timeline = build_ground_truth(self.complete_traces(), self.grid)
        self.assertEqual(timeline.cells_at(-0.1), frozenset())
        self.assertEqual(timeline.cells_at(0), frozenset((0, 2)))
        self.assertEqual(timeline.cells_at(1), frozenset((0, 1, 2)))
        self.assertEqual(timeline.cells_at(2), frozenset((0, 1, 2, 3)))
        self.assertEqual(timeline.t_cover, 2.0)
        self.assertEqual(timeline.steps[0].time_s, 0.0)
        self.assertEqual(timeline.steps[0].cell_ids_added, (0, 2))

    def test_trace_checker_requires_full_coverage_and_post_window(self):
        report = check_traces(self.complete_traces(), self.grid)
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.t_cover_s, 2.0)
        self.assertEqual(report.common_end_time_s, 22.0)
        self.assertEqual(report.missing_cell_ids, ())

        too_short = self.complete_traces()
        too_short["1"] = too_short["1"][:-1]
        report = check_traces(too_short, self.grid)
        self.assertFalse(report.valid)
        self.assertIn(
            "post_coverage_window_too_short",
            {issue.code for issue in report.issues},
        )

        incomplete = {"0": [sample("0", 0, 0.1, 0.1)]}
        report = check_traces(incomplete, self.grid, post_coverage_window_s=None)
        self.assertFalse(report.valid)
        self.assertEqual(report.covered_cell_count, 1)
        self.assertIn("incomplete_coverage", {issue.code for issue in report.issues})

    def test_optional_motion_check_distinguishes_samples_from_motion(self):
        report = check_traces(
            self.complete_traces(),
            self.grid,
            require_motion_after_cover=True,
        )
        self.assertFalse(report.valid)
        self.assertIn(
            "no_motion_through_post_coverage_window",
            {issue.code for issue in report.issues},
        )

    def test_ground_truth_can_start_at_coverage_epoch(self):
        traces = {
            "0": [
                sample("0", 0, 0.1, 0.1),
                sample("0", 1, 1.1, 0.1),
                sample("0", 2, 1.1, 1.1),
                sample("0", 3, 0.1, 1.1),
            ]
        }
        timeline = build_ground_truth(
            traces, self.grid, coverage_start_time_s=1.5
        )
        self.assertEqual(timeline.cells_at(1.49), frozenset())
        self.assertEqual(timeline.cells_at(1.5), frozenset((1,)))
        self.assertEqual(timeline.cells_at(3), frozenset((1, 2, 3)))
        self.assertNotIn(0, timeline.covered_cells)

    def test_checker_rejects_coverage_start_after_any_trace_ends(self):
        traces = self.complete_traces()
        traces["1"] = traces["1"][:-1]
        report = check_traces(
            traces,
            self.grid,
            post_coverage_window_s=0,
            coverage_start_time_s=3.0,
        )
        self.assertFalse(report.valid)
        self.assertIn(
            "coverage_start_after_trace_end",
            {issue.code for issue in report.issues},
        )

        for invalid in (float("nan"), float("inf"), -1.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                check_traces(
                    self.complete_traces(),
                    self.grid,
                    post_coverage_window_s=0,
                    coverage_start_time_s=invalid,
                )


class EventAndCarTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridSpec(0, 0, 2, 2, 2, 2)
        self.ground_truth = GroundTruthTimeline(
            self.grid,
            {0: 0.0, 1: 1.0, 2: 2.0, 3: 2.0},
        )

    def event(
        self,
        node,
        time_s,
        name,
        size,
        added=(),
        serialized=None,
        base="elapsed",
        sequence=None,
        replica_version=None,
    ):
        return ReplicaEvent(
            time_s=float(time_s),
            node=str(node),
            event=name,
            replica_size=size,
            mutation_sequence=sequence,
            replica_version=replica_version,
            cell_ids_added=tuple(added),
            serialized_state_size=serialized,
            time_base=base,
        )

    def valid_events(self):
        return [
            self.event("0", 0, "local_coverage", 1, (0,)),
            self.event("0", 1, "remote_merge", 2, (1,)),
            self.event("0", 2, "remote_merge", 3, (2,)),
            self.event("0", 7, "remote_merge", 4, (3,)),
            self.event("1", 0, "local_coverage", 1, (2,)),
            self.event("1", 2, "remote_merge", 2, (0,)),
            self.event("1", 7, "remote_merge", 3, (1,)),
        ]

    def test_event_log_accepts_key_value_csv_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_7.log.events"
            path.write_text(
                "timestamp_unix_s=100.25, event=local_coverage, node=7, "
                "cell_ids_added=0|2, replica_size=2, mutation_sequence=1\n"
                '{"elapsed_s": 1.5, "event": "dissemination_trigger", '
                '"node": "7", "replica_size": 2, "replica_version": 1, '
                '"serialized_state_size": 4}\n',
                encoding="utf-8",
            )
            events = read_event_log(path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].time_base, "unix")
        self.assertEqual(events[0].cell_ids_added, (0, 2))
        self.assertEqual(events[0].mutation_sequence, 1)
        self.assertEqual(events[1].time_base, "elapsed")
        self.assertEqual(events[1].mutation_sequence, 1)
        self.assertEqual(events[1].serialized_state_size, 4)

    def test_event_log_rejects_conflicting_sequence_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_0.log.events"
            path.write_text(
                "timestamp_unix_s=100, event=local_coverage, "
                "mutation_sequence=1, replica_version=2, "
                "cell_ids_added=0, replica_size=1\n",
                encoding="utf-8",
            )
            with self.assertRaises(TraceFormatError):
                read_event_log(path)

    def test_experiment_clock_exposes_trace_relative_coverage_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_clock.json"
            path.write_text(
                json.dumps(
                    {
                        "trace_start_unix_s": 1000.25,
                        "coverage_start_unix_s": 1002.75,
                    }
                ),
                encoding="utf-8",
            )
            clock = read_experiment_clock(path)
        self.assertEqual(clock.trace_start_unix_s, 1000.25)
        self.assertEqual(clock.coverage_start_trace_s, 2.5)

    def test_experiment_clock_requires_coverage_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment_clock.json"
            for payload in (
                {"trace_start_unix_s": 1000.25},
                {
                    "trace_start_unix_s": 1000.25,
                    "coverage_start_unix_s": None,
                },
            ):
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(TraceFormatError):
                        read_experiment_clock(path)

    def test_event_log_rejects_duplicate_or_invalid_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_0.log.events"
            path.write_text(
                "0, event=local_coverage, cell_ids_added=1|1, replica_size=1\n",
                encoding="utf-8",
            )
            with self.assertRaises(TraceFormatError):
                read_event_log(path, default_time_base="elapsed")

            for invalid_cells in ([True], [1.5]):
                with self.subTest(invalid_cells=invalid_cells):
                    path.write_text(
                        json.dumps(
                            {
                                "elapsed_s": 0,
                                "event": "local_coverage",
                                "cell_ids_added": invalid_cells,
                                "replica_size": 1,
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(TraceFormatError):
                        read_event_log(path)

    def test_absolute_events_require_explicit_trace_epoch(self):
        events = [
            self.event("0", 100.0, "local_coverage", 1, (0,), base="monotonic")
        ]
        with self.assertRaises(TimeAlignmentError):
            build_replica_timelines(events, 4)
        timelines, issues = build_replica_timelines(
            events, 4, trace_epoch_s=100.0
        )
        self.assertEqual(issues, ())
        self.assertEqual(timelines["0"].size_at(0), 1)

    def test_car_at_tcover_and_postcoverage_checkpoints(self):
        analysis = analyze_car(
            self.ground_truth,
            self.valid_events(),
            checkpoint_offsets_s=(0, 5),
            expected_nodes=("0", "1"),
        )
        self.assertIsInstance(analysis, CarAnalysis)
        self.assertTrue(analysis.valid, analysis.issues)
        self.assertEqual(analysis.t_cover_s, 2.0)
        self.assertAlmostEqual(analysis.samples[0].per_node_car["0"], 0.75)
        self.assertAlmostEqual(analysis.samples[0].per_node_car["1"], 0.5)
        self.assertAlmostEqual(analysis.samples[0].swarm_car, 0.625)
        self.assertAlmostEqual(analysis.samples[1].swarm_car, 0.875)

    def test_zero_ground_truth_is_nan(self):
        timelines = {"0": ReplicaTimeline("0", (), ())}
        samples, issues = compute_car_samples(
            self.ground_truth, timelines, (-1.0,)
        )
        self.assertEqual(issues, ())
        self.assertTrue(math.isnan(samples[0].per_node_car["0"]))
        self.assertTrue(math.isnan(samples[0].swarm_car))
        self.assertIsNone(samples[0].to_dict()["swarm_car"])

    def test_car_above_one_is_not_clamped_and_is_flagged(self):
        timelines = {"0": ReplicaTimeline("0", (0.0,), (2,))}
        samples, issues = compute_car_samples(
            self.ground_truth, timelines, (0.0,)
        )
        self.assertEqual(samples[0].per_node_car["0"], 2.0)
        self.assertIn("car_exceeds_one", {issue.code for issue in issues})

    def test_versioned_log_validates_union_growth_and_encoding_size(self):
        events = [
            self.event("0", 0, "local_coverage", 2, (0,), sequence=1),
            self.event("0", 1, "remote_merge", 1, (0,), sequence=2),
            self.event("0", 2, "dissemination_trigger", 1, serialized=3),
        ]
        timelines, issues = build_replica_timelines(events, 4)
        codes = {issue.code for issue in issues}
        self.assertIn("replica_size_union_mismatch", codes)
        self.assertIn("cell_added_twice", codes)
        self.assertIn("mutation_without_growth", codes)
        self.assertIn("serialized_state_size_mismatch", codes)
        self.assertEqual(timelines["0"].sizes, (1, 1))

    def test_legacy_interleaving_uses_timestamp_order_and_monotonic_union(self):
        # Old callbacks captured size/timestamp after releasing the GSet lock.
        # A later remote mutation could therefore be logged before an older
        # local mutation, with reported sizes 2 then 1. Those observations are
        # deliberately ignored; the added-cell union remains monotonic.
        events = [
            self.event("0", 1, "remote_merge", 2, (1,)),
            self.event("0", 2, "local_coverage", 1, (0,)),
        ]
        timelines, issues = build_replica_timelines(events, 4)
        self.assertEqual(issues, ())
        self.assertEqual(timelines["0"].times_s, (1.0, 2.0))
        self.assertEqual(timelines["0"].sizes, (1, 2))

    def test_mutation_sequence_orders_out_of_order_log_lines(self):
        events = [
            self.event("0", 1, "remote_merge", 2, (1,), sequence=2),
            self.event("0", 0, "local_coverage", 1, (0,), sequence=1),
        ]
        timelines, issues = build_replica_timelines(events, 4)
        self.assertEqual(issues, ())
        self.assertEqual(timelines["0"].times_s, (0.0, 1.0))
        self.assertEqual(timelines["0"].sizes, (1, 2))

    def test_cpp_replica_version_alias_orders_parsed_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_0.log.events"
            path.write_text(
                "101, event=remote_merge, node=0, cell_ids_added=1, "
                "replica_size=2, replica_version=2\n"
                "100, event=local_coverage, node=0, cell_ids_added=0, "
                "replica_size=1, replica_version=1\n",
                encoding="utf-8",
            )
            events = read_event_log(path)
        timelines, issues = build_replica_timelines(
            events, 4, trace_epoch_s=100.0
        )
        self.assertEqual(issues, ())
        self.assertEqual(timelines["0"].times_s, (0.0, 1.0))
        self.assertEqual(timelines["0"].sizes, (1, 2))

    def test_mutation_sequence_must_be_contiguous_from_one(self):
        events = [
            self.event("0", 0, "local_coverage", 1, (0,), sequence=1),
            self.event("0", 1, "remote_merge", 2, (1,), sequence=3),
        ]
        _, issues = build_replica_timelines(events, 4)
        self.assertIn(
            "invalid_mutation_sequence", {issue.code for issue in issues}
        )

    def test_post_merge_network_observation_does_not_create_false_growth_error(self):
        events = [
            self.event("0", 0, "local_coverage", 1, (0,)),
            self.event("0", 0.5, "network_receive", 4),
            self.event(
                "0", 0.75, "dissemination_trigger", 1, serialized=2
            ),
            self.event("0", 1, "remote_merge", 2, (1,)),
            self.event("0", 2, "network_receive", 1),
        ]
        timelines, issues = build_replica_timelines(events, 4)
        self.assertEqual(issues, ())
        self.assertEqual(timelines["0"].times_s, (0.0, 1.0))
        self.assertEqual(timelines["0"].sizes, (1, 2))
        self.assertEqual(timelines["0"].size_at(1), 2)

    def test_event_cell_must_belong_to_grid(self):
        _, issues = build_replica_timelines(
            [self.event("0", 0, "local_coverage", 1, (99,))],
            4,
        )
        self.assertIn("event_cell_outside_grid", {issue.code for issue in issues})

    def test_incomplete_ground_truth_has_no_tcover_analysis(self):
        incomplete = GroundTruthTimeline(self.grid, {0: 0.0})
        analysis = analyze_car(incomplete, self.valid_events(), (0,))
        self.assertIsNone(analysis.t_cover_s)
        self.assertFalse(analysis.valid)
        self.assertEqual(analysis.samples, ())


class CliTests(unittest.TestCase):
    def test_check_traces_cli_emits_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_0.csv"
            path.write_text(
                "time_s,x_m,y_m,z_m\n"
                "0,0.1,0.1,0\n"
                "1,1.1,0.1,0\n"
                "2,1.1,1.1,0\n"
                "3,0.1,1.1,0\n"
                "23,0.1,1.1,0\n",
                encoding="utf-8",
            )
            grid_config = Path(tmp) / "node_config.json"
            grid_config.write_text(
                json.dumps(
                    {
                        "grid": {
                            "origin_x_m": 0,
                            "origin_y_m": 0,
                            "width_m": 2,
                            "height_m": 2,
                            "rows": 2,
                            "cols": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "check-traces",
                        "--grid-config",
                        str(grid_config),
                        "--coverage-start-time-s",
                        "0",
                        str(path),
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["t_cover_s"], 3.0)

    def test_analyze_run_dir_auto_discovers_shared_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            trace_dir = run_dir / "mobility_traces"
            trace_dir.mkdir()
            (trace_dir / "node_0.csv").write_text(
                "time_s,x_m,y_m,z_m\n"
                "0,0.1,0.1,0\n"
                "1,1.1,0.1,0\n"
                "2,1.1,1.1,0\n"
                "3,0.1,1.1,0\n"
                "23,0.1,1.1,0\n",
                encoding="utf-8",
            )
            (run_dir / "node_0.log.events").write_text(
                "100, event=local_coverage, node=0, cell_ids_added=0, replica_size=1\n"
                "101, event=local_coverage, node=0, cell_ids_added=1, replica_size=2\n"
                "102, event=local_coverage, node=0, cell_ids_added=3, replica_size=3\n"
                "103, event=local_coverage, node=0, cell_ids_added=2, replica_size=4\n",
                encoding="utf-8",
            )
            (run_dir / "experiment_clock.json").write_text(
                json.dumps(
                    {
                        "trace_start_unix_s": 100.0,
                        "coverage_start_unix_s": 100.0,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "node_config.json").write_text(
                json.dumps(
                    {
                        "grid": {
                            "origin_x_m": 0,
                            "origin_y_m": 0,
                            "width_m": 2,
                            "height_m": 2,
                            "rows": 2,
                            "cols": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "analyze",
                        "--run-dir",
                        str(run_dir),
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0, payload)
        self.assertEqual(payload["experiment_clock"]["coverage_start_trace_s"], 0.0)
        self.assertEqual(payload["car"]["samples"][0]["swarm_car"], 1.0)


if __name__ == "__main__":
    unittest.main()
