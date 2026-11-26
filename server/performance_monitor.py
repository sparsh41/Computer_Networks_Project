import threading

class PerformanceMonitor:
    """Tracks active connections and files being transferred; no CSV logging."""
    def __init__(self, log_dir=None):
        self.active_files = set()
        self._active_lock = threading.Lock()
        self.active_clients = 0
        self._clients_lock = threading.Lock()

    def client_connected(self):
        with self._clients_lock:
            self.active_clients += 1
            return self.active_clients

    def client_disconnected(self):
        with self._clients_lock:
            self.active_clients = max(0, self.active_clients - 1)
            return self.active_clients

    def mark_active(self, path):
        with self._active_lock:
            self.active_files.add(path)

    def unmark_active(self, path):
        with self._active_lock:
            self.active_files.discard(path)

    def is_active(self, path):
        with self._active_lock:
            return path in self.active_files

    def close(self):
        pass