import tempfile
import threading
import time
import unittest
from pathlib import Path

from client import FileClient
from server import FileServer


class TransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server_dir = tempfile.TemporaryDirectory()
        self.download_dir = tempfile.TemporaryDirectory()

        sample = Path(self.server_dir.name) / "example.txt"
        sample.write_text("network-data", encoding="utf-8")

        self.server = FileServer("127.0.0.1", 0, self.server_dir.name)
        self.thread = threading.Thread(target=self.server.start, daemon=True)
        self.thread.start()
        ready = self.server.ready_event.wait(timeout=3)
        self.assertTrue(ready, "Server did not start in time")
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.server.stop()
        self.thread.join(timeout=2)
        self.server_dir.cleanup()
        self.download_dir.cleanup()

    def test_list_and_download(self) -> None:
        client = FileClient("127.0.0.1", self.server.port)
        files = client.list_files()
        self.assertEqual(files, ["example.txt"])

        destination = Path(self.download_dir.name)
        downloaded = client.download_file("example.txt", str(destination))
        self.assertTrue(downloaded.exists())
        self.assertEqual(downloaded.read_text(encoding="utf-8"), "network-data")


if __name__ == "__main__":
    unittest.main()
