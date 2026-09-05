import csv
import math
import os
import pickle
import socket
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from classes.auxiliar.virtual_gps import (  # noqa: E402
    GPS_V1_MAGIC,
    GPS_V1_RESPONSE,
    LEGACY_LENGTH,
    MAX_LEGACY_PAYLOAD,
    VirtualGPS,
)
from evaluation import gps_logger  # noqa: E402


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


class VirtualGPSTests(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()

    def make_server(self, position=None):
        tag = "codex_gps_" + uuid.uuid4().hex
        server = VirtualGPS(tag, 0)
        if position is not None:
            server.set_position(position)
        server.start()
        self.servers.append(server)
        # start() is a readiness barrier: once it returns, bind() and listen()
        # have both completed and a client may connect immediately.
        self.assertTrue(os.path.exists(server.sock_path))
        return server

    def connect(self, server):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(server.sock_path)
        return client

    def legacy_position(self, server, fragmented_header=False):
        request = pickle.dumps([server.tag, "GET_POSITION"])
        client = self.connect(server)
        try:
            header = LEGACY_LENGTH.pack(len(request))
            if fragmented_header:
                client.sendall(header[:2])
                time.sleep(0.01)
                client.sendall(header[2:])
            else:
                client.sendall(header)
            client.sendall(request)
            response_header = recv_exact(client, LEGACY_LENGTH.size)
            self.assertIsNotNone(response_header)
            response_size, = LEGACY_LENGTH.unpack(response_header)
            response = recv_exact(client, response_size)
            self.assertIsNotNone(response)
            return pickle.loads(response)
        finally:
            client.close()

    def gps1_position(self, server, fragmented_request=False):
        client = self.connect(server)
        try:
            if fragmented_request:
                client.sendall(GPS_V1_MAGIC[:1])
                time.sleep(0.01)
                client.sendall(GPS_V1_MAGIC[1:])
            else:
                client.sendall(GPS_V1_MAGIC)
            response = recv_exact(client, GPS_V1_RESPONSE.size)
            self.assertIsNotNone(response)
            return GPS_V1_RESPONSE.unpack(response)
        finally:
            client.close()

    def test_legacy_pickle_protocol_is_preserved_with_fragmented_header(self):
        server = self.make_server([12.5, 23.75, 4.0])
        self.assertEqual(
            self.legacy_position(server, fragmented_header=True),
            [12.5, 23.75, 4.0],
        )

    def test_gps1_returns_fixed_network_order_frame(self):
        server = self.make_server([1.25, -2.5, 3.75])
        magic, status, x, y, z = self.gps1_position(
            server, fragmented_request=True
        )
        self.assertEqual(magic, GPS_V1_MAGIC)
        self.assertEqual(status, 1)
        self.assertEqual((x, y, z), (1.25, -2.5, 3.75))
        self.assertEqual(GPS_V1_RESPONSE.size, 29)

    def test_gps1_normalizes_a_two_dimensional_position(self):
        server = self.make_server((7.0, 8.0))
        _, status, x, y, z = self.gps1_position(server)
        self.assertEqual(status, 1)
        self.assertEqual((x, y, z), (7.0, 8.0, 0.0))

    def test_gps1_marks_missing_or_non_finite_positions_invalid(self):
        server = self.make_server()
        _, status, x, y, z = self.gps1_position(server)
        self.assertEqual(status, 0)
        self.assertEqual((x, y, z), (0.0, 0.0, 0.0))

        server.set_position([math.nan, 1.0, 0.0])
        _, status, _, _, _ = self.gps1_position(server)
        self.assertEqual(status, 0)

    def test_bad_legacy_frame_does_not_stop_the_server(self):
        server = self.make_server([3.0, 4.0, 5.0])
        client = self.connect(server)
        client.sendall(LEGACY_LENGTH.pack(MAX_LEGACY_PAYLOAD + 1))
        client.close()

        self.assertEqual(self.legacy_position(server), [3.0, 4.0, 5.0])

        malformed = b"this is not a pickle"
        client = self.connect(server)
        client.sendall(LEGACY_LENGTH.pack(len(malformed)) + malformed)
        client.close()

        self.assertEqual(self.legacy_position(server), [3.0, 4.0, 5.0])

    def test_shutdown_unblocks_listener_and_removes_socket(self):
        server = self.make_server([0.0, 0.0])
        thread = server.virtual_gps_thread
        server.shutdown()
        self.assertFalse(thread.is_alive())

        deadline = time.monotonic() + 1.0
        while os.path.exists(server.sock_path) and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(os.path.exists(server.sock_path))

    def test_start_propagates_listener_setup_failure(self):
        server = VirtualGPS("unused", 0)
        server.sock_path = "/path/that/does/not/exist/gps.sock"
        with self.assertRaises(OSError):
            server.start()
        server.shutdown()

    def test_start_propagates_socket_creation_failure(self):
        server = VirtualGPS("unused_creation_failure", 0)
        with mock.patch(
            "classes.auxiliar.virtual_gps.socket.socket",
            side_effect=OSError("socket creation failed"),
        ):
            with self.assertRaisesRegex(OSError, "socket creation failed"):
                server.start()
        server.shutdown()


class GPSLoggerTests(unittest.TestCase):
    def test_csv_keeps_relative_time_and_adds_unix_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "gps.csv"
            argv = [
                "gps_logger.py",
                "--tag",
                "node0",
                "--node",
                "0",
                "--out",
                str(output),
                "--interval",
                "0.01",
                "--duration",
                "0.03",
            ]
            before = time.time()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                gps_logger, "poll_once", return_value=(1.0, 2.0, 3.0, 1)
            ):
                self.assertEqual(gps_logger.main(), 0)
            after = time.time()

            with output.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))

            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(
                list(rows[0].keys()),
                [
                    "time_s",
                    "timestamp_unix_s",
                    "node",
                    "x_m",
                    "y_m",
                    "z_m",
                    "ok",
                ],
            )
            self.assertGreaterEqual(float(rows[0]["time_s"]), 0.0)
            self.assertGreaterEqual(float(rows[0]["timestamp_unix_s"]), before)
            self.assertLessEqual(float(rows[0]["timestamp_unix_s"]), after)
            self.assertEqual(rows[0]["node"], "0")
            self.assertEqual(rows[0]["ok"], "1")


if __name__ == "__main__":
    unittest.main()
