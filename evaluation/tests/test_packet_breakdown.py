import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


EVALUATION_DIR = Path(__file__).resolve().parents[1]
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from parse_network import parse_network_overhead
from process_pcaps import (
    _classify_batadv,
    _classify_protocol_type,
    classification_config_for_app,
)


class ProtocolClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apps_path = EVALUATION_DIR / "apps.json"
        cls.apps = json.loads(apps_path.read_text(encoding="utf-8"))

    def test_rapid_and_trickle_type_mappings(self):
        rapid = classification_config_for_app("rapid", self.apps)
        trickle = classification_config_for_app("trickle", self.apps)

        self.assertEqual(_classify_protocol_type(1, rapid)[0], "payload")
        self.assertEqual(_classify_protocol_type(4, rapid)[0], "control")
        self.assertEqual(_classify_protocol_type(1, trickle)[0], "control")
        self.assertEqual(_classify_protocol_type(2, trickle)[0], "payload")
        self.assertEqual(_classify_protocol_type(9, rapid)[0], "unclassified")

    def test_app_without_classifier_keeps_legacy_total_available(self):
        config = {"apps": {"legacy": {"tshark_display_filter": "udp.port==5001"}}}
        self.assertEqual(
            classification_config_for_app("legacy", config)["mode"],
            "none",
        )

    def test_batman_payload_control_and_unknown(self):
        batman = classification_config_for_app("broadcast", self.apps)

        self.assertEqual(
            _classify_batadv({"batadv.batman.packet_type": "0"}, batman)[0],
            "control",
        )
        self.assertEqual(
            _classify_batadv(
                {
                    "batadv.batman.packet_type": "1",
                    "udp.dstport": "5001",
                },
                batman,
            )[0],
            "payload",
        )
        self.assertEqual(
            _classify_batadv(
                {
                    "batadv.batman.packet_type": "1",
                    "arp.opcode": "1",
                },
                batman,
            )[0],
            "control",
        )
        self.assertEqual(
            _classify_batadv({"batadv.batman.packet_type": "255"}, batman)[0],
            "unclassified",
        )


class RunAggregationTests(unittest.TestCase):
    FIELDNAMES = [
        "node",
        "status",
        "frames",
        "bytes",
        "payload_frames",
        "control_frames",
        "unclassified_frames",
        "classification_residual",
        "classification_status",
        "unclassified_reasons_json",
        "unclassified_sample_frames_json",
    ]

    def _parse_rows(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with (run_dir / "pcap_metrics.csv").open(
                "w", newline="", encoding="utf-8"
            ) as output:
                writer = csv.DictWriter(output, fieldnames=self.FIELDNAMES)
                writer.writeheader()
                writer.writerows(rows)
            return parse_network_overhead(run_dir)

    def test_categories_close_over_the_same_total(self):
        result = self._parse_rows(
            [
                {
                    "node": "node_0",
                    "status": "ok",
                    "frames": 10,
                    "bytes": 600,
                    "payload_frames": 6,
                    "control_frames": 3,
                    "unclassified_frames": 1,
                    "classification_residual": 0,
                    "classification_status": "warning_unclassified",
                    "unclassified_reasons_json": '{"unknown_type": 1}',
                    "unclassified_sample_frames_json": '{"unknown_type": [7]}',
                },
                {
                    "node": "node_1",
                    "status": "ok",
                    "frames": 20,
                    "bytes": 1200,
                    "payload_frames": 12,
                    "control_frames": 7,
                    "unclassified_frames": 1,
                    "classification_residual": 0,
                    "classification_status": "warning_unclassified",
                    "unclassified_reasons_json": '{"unknown_type": 1}',
                    "unclassified_sample_frames_json": '{"unknown_type": [11]}',
                },
            ]
        )

        self.assertEqual(result["total_packets"], 30)
        self.assertEqual(result["total_payload_packets"], 18)
        self.assertEqual(result["total_control_packets"], 10)
        self.assertEqual(result["total_unclassified_packets"], 2)
        self.assertEqual(result["classification_residual"], 0)
        self.assertEqual(result["classification_status"], "warning_unclassified")

    def test_legacy_rows_preserve_total_without_inventing_breakdown(self):
        result = self._parse_rows(
            [{"node": "node_0", "status": "ok", "frames": 9, "bytes": 540}]
        )

        self.assertEqual(result["total_packets"], 9)
        self.assertEqual(result["classification_status"], "unavailable")
        self.assertNotIn("total_payload_packets", result)

    def test_nonzero_residual_marks_breakdown_invalid(self):
        result = self._parse_rows(
            [
                {
                    "node": "node_0",
                    "status": "ok",
                    "frames": 10,
                    "bytes": 600,
                    "payload_frames": 6,
                    "control_frames": 3,
                    "unclassified_frames": 0,
                    "classification_residual": 1,
                    "classification_status": "error_classification_residual",
                }
            ]
        )

        self.assertEqual(result["classification_residual"], 1)
        self.assertEqual(result["classification_status"], "error_classification_residual")


if __name__ == "__main__":
    unittest.main()
