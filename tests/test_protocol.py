import json
import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from client_handler import recv_json, send_json, safe_name

class ProtocolTests(unittest.TestCase):
    def test_json_frame(self):
        left, right = socket.socketpair()
        try:
            send_json(left, {"command": "dir", "path": "."})
            self.assertEqual(recv_json(right), {"command": "dir", "path": "."})
        finally:
            left.close()
            right.close()

    def test_safe_name(self):
        self.assertEqual(safe_name("report 1.pdf"), "report 1.pdf")
        for bad in ("../secret", "a/b", "..", ""):
            with self.assertRaises(ValueError):
                safe_name(bad)

if __name__ == "__main__":
    unittest.main()
