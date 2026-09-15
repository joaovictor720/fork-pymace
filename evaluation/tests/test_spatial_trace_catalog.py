import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
CATALOG_DIR = ROOT_DIR / "evaluation" / "trace_catalogs" / "spatial_grid_1km_24x24"


class SpatialTraceCatalogTests(unittest.TestCase):
    def test_catalog_shape_and_first_t_cover_range(self):
        catalog = json.loads(
            (CATALOG_DIR / "catalog.json").read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["schema"], "mace_spatial_trace_catalog_v1")
        self.assertEqual(catalog["run_count"], 10)
        self.assertEqual(catalog["node_count"], 50)
        self.assertEqual(catalog["coverage_node_count"], 10)
        self.assertEqual(catalog["density_prefixes"], [10, 20, 30, 40, 50])
        self.assertEqual(catalog["grid"]["cell_count"], 576)
        self.assertEqual(catalog["coverage_speed_mps"], 20.0)
        self.assertEqual(catalog["patrol_speed_mps"], 20.0)

        accepted_min, accepted_max = catalog["accepted_t_cover_range_s"]
        for run in catalog["runs"]:
            run_dir = CATALOG_DIR / run["run"]
            self.assertTrue((run_dir / "manifest.json").exists())
            self.assertEqual(len(list(run_dir.glob("node_*.csv"))), 50)

            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(manifest["nodes"]), 50)
            self.assertEqual(manifest["grid"]["rows"], 24)
            self.assertEqual(manifest["grid"]["cols"], 24)
            self.assertEqual(manifest["coverage_speed_mps"], 20.0)
            self.assertEqual(manifest["patrol_speed_mps"], 20.0)
            coverage_counts = [
                node["coverage_cell_count"]
                for node in manifest["nodes"][:10]
            ]
            self.assertEqual(sum(coverage_counts), 576)
            self.assertLessEqual(max(coverage_counts) - min(coverage_counts), 1)
            first_t_cover = manifest["prefix_validations"]["10"]["t_cover_s"]
            self.assertGreaterEqual(first_t_cover, accepted_min)
            self.assertLessEqual(first_t_cover, accepted_max)
            for prefix in ("10", "20", "30", "40", "50"):
                validation = manifest["prefix_validations"][prefix]
                self.assertEqual(validation["covered_cell_count"], 576)
                self.assertEqual(validation["total_cell_count"], 576)


if __name__ == "__main__":
    unittest.main()
