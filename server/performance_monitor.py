import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

class PerformanceMonitor:
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / "transfers.csv"
        self.lock = threading.Lock()
        self.active_files = set()
        self._active_lock = threading.Lock()
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(
                    ["timestamp_utc", "operation", "file", "bytes", "seconds",
                     "throughput_MB_s", "username"])

    def record(self, operation, filename, byte_count, seconds, username):
        with self.lock, self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                datetime.now(timezone.utc).isoformat(), operation, filename,
                byte_count, round(seconds, 6),
                round(byte_count / (1024*1024) / seconds, 3), username
            ])

    def mark_active(self, path):
        with self._active_lock:
            self.active_files.add(path)

    def unmark_active(self, path):
        with self._active_lock:
            self.active_files.discard(path)

    def close(self):
        pass
