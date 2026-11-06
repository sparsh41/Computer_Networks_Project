import socket
import unittest

from protocol import ProtocolError, recv_message, send_message


class ProtocolTests(unittest.TestCase):
    def test_send_recv_roundtrip(self) -> None:
        left, right = socket.socketpair()
        with left, right:
            send_message(left, {"command": "LIST"})
            received = recv_message(right)
        self.assertEqual(received, {"command": "LIST"})

    def test_invalid_payload_raises(self) -> None:
        left, right = socket.socketpair()
        with left, right:
            left.sendall((5).to_bytes(4, "big") + b"12345")
            with self.assertRaises(ProtocolError):
                recv_message(right)


if __name__ == "__main__":
    unittest.main()
