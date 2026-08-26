import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
EVALUATION_DIR = ROOT_DIR / "evaluation"
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from mobility_trace import TRACE_POLICY, file_sha256
from seed_utils import central_seed, derive_seed


class SeedPolicyTests(unittest.TestCase):
    def test_central_seed_prefers_top_level_and_falls_back_to_nodes_seed(self):
        self.assertEqual(central_seed({"seed": 123, "nodes": {"seed": 7}}), 123)
        self.assertEqual(central_seed({"nodes": {"seed": 7}}), 7)

    def test_derived_seeds_are_stable_and_nonzero_by_default(self):
        seed_a = derive_seed(22, "application")
        seed_b = derive_seed(22, "application")
        seed_c = derive_seed(22, "mobility", 0)

        self.assertEqual(seed_a, seed_b)
        self.assertNotEqual(seed_a, seed_c)
        self.assertGreater(seed_a, 0)

    def test_generated_configs_are_driven_by_central_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario_dir = Path(tmp)
            shutil.copyfile(
                ROOT_DIR / "scenarios/debug_ip/scenario.json",
                scenario_dir / "scenario.json",
            )

            scenario = json.loads(
                (scenario_dir / "scenario.json").read_text(encoding="utf-8")
            )
            scenario["seed"] = 909
            scenario["nodes"]["seed"] = 7
            (scenario_dir / "scenario.json").write_text(
                json.dumps(scenario), encoding="utf-8"
            )

            subprocess.run(
                [
                    sys.executable,
                    str(EVALUATION_DIR / "generate_scenario.py"),
                    str(scenario_dir),
                    "rapid",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            first_mace = (scenario_dir / "mace.json").read_text(encoding="utf-8")
            first_trace_manifest = (
                scenario_dir / "mobility_traces" / "manifest.json"
            ).read_text(encoding="utf-8")
            first_trace = (
                scenario_dir / "mobility_traces" / "node_0.csv"
            ).read_text(encoding="utf-8")

            result_dir = scenario_dir / "result"
            result_dir.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    str(EVALUATION_DIR / "generate_node_config.py"),
                    str(scenario_dir / "scenario.json"),
                    str(result_dir / "node_config.json"),
                    str(result_dir),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            first_node_config = (result_dir / "node_config.json").read_text(
                encoding="utf-8"
            )

            subprocess.run(
                [
                    sys.executable,
                    str(EVALUATION_DIR / "generate_scenario.py"),
                    str(scenario_dir),
                    "rapid",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(EVALUATION_DIR / "generate_node_config.py"),
                    str(scenario_dir / "scenario.json"),
                    str(result_dir / "node_config.json"),
                    str(result_dir),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

            self.assertEqual(
                first_mace,
                (scenario_dir / "mace.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                first_trace_manifest,
                (scenario_dir / "mobility_traces" / "manifest.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                first_trace,
                (scenario_dir / "mobility_traces" / "node_0.csv").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                first_node_config,
                (result_dir / "node_config.json").read_text(encoding="utf-8"),
            )

            mace = json.loads(first_mace)
            trace_manifest = json.loads(first_trace_manifest)
            node_config = json.loads(first_node_config)
            mobility_seeds = [
                node["extra"]["mobility"]["seed"]
                for node in mace["nodes"]
            ]
            mobility_traces = [
                node["extra"]["mobility"]["trace_file"]
                for node in mace["nodes"]
            ]

            self.assertEqual(node_config["central_seed"], 909)
            self.assertEqual(node_config["seed"], derive_seed(909, "application"))
            self.assertTrue(all(seed > 0 for seed in mobility_seeds))
            self.assertEqual(len(mobility_seeds), len(set(mobility_seeds)))
            self.assertEqual(trace_manifest["policy"], TRACE_POLICY)
            self.assertEqual(len(trace_manifest["nodes"]), len(mace["nodes"]))
            self.assertTrue(all(Path(path).exists() for path in mobility_traces))
            for node in mace["nodes"]:
                mobility = node["extra"]["mobility"]
                self.assertTrue(mobility["deterministic_replay"])
                self.assertEqual(mobility["trace_policy"], TRACE_POLICY)
                self.assertEqual(
                    mobility["trace_sha256"],
                    file_sha256(Path(mobility["trace_file"])),
                )


if __name__ == "__main__":
    unittest.main()
