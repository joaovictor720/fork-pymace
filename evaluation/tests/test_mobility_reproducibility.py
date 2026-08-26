import sys
import unittest
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from classes.mobility.pymobility.models.mobility import random_waypoint


class RandomWaypointSeedTests(unittest.TestCase):
    def _take(self, model, steps):
        return [next(model).copy() for _ in range(steps)]

    def assert_same_trace(self, left, right):
        self.assertEqual(len(left), len(right))
        for left_step, right_step in zip(left, right):
            self.assertTrue(np.allclose(left_step, right_step))

    def test_same_seed_replays_same_trace(self):
        kwargs = {
            "nr_nodes": 3,
            "dimensions": (300, 300),
            "velocity": (0.8, 1.2),
            "wt_max": 0,
            "seed": 12345,
        }

        trace_a = self._take(random_waypoint(**kwargs), 20)
        trace_b = self._take(random_waypoint(**kwargs), 20)

        self.assert_same_trace(trace_a, trace_b)

    def test_different_seed_changes_trace(self):
        base_kwargs = {
            "nr_nodes": 3,
            "dimensions": (300, 300),
            "velocity": (0.8, 1.2),
            "wt_max": 0,
        }

        trace_a = self._take(random_waypoint(**base_kwargs, seed=12345), 20)
        trace_b = self._take(random_waypoint(**base_kwargs, seed=54321), 20)

        self.assertTrue(
            any(not np.allclose(a, b) for a, b in zip(trace_a, trace_b))
        )

    def test_seeded_instances_do_not_share_rng_state(self):
        kwargs = {
            "nr_nodes": 1,
            "dimensions": (300, 300),
            "velocity": (0.8, 1.2),
            "wt_max": 0,
        }
        model_1 = random_waypoint(**kwargs, seed=111)
        model_2 = random_waypoint(**kwargs, seed=222)

        interleaved_1 = []
        interleaved_2 = []
        for _ in range(20):
            interleaved_2.append(next(model_2).copy())
            interleaved_1.append(next(model_1).copy())

        solo_1 = self._take(random_waypoint(**kwargs, seed=111), 20)
        solo_2 = self._take(random_waypoint(**kwargs, seed=222), 20)

        self.assert_same_trace(interleaved_1, solo_1)
        self.assert_same_trace(interleaved_2, solo_2)


if __name__ == "__main__":
    unittest.main()
