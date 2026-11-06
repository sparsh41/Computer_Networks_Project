import argparse
import socket
import threading
from pathlib import Path
from typing import Optional

from protocol import ProtocolError, recv_message, send_message

CHUNK_SIZE = 64 * 1024


class FileServer:
    def __init__(self, host: str, port: int, storage_dir: str, backlog: int = 20) -> None:
        self.host = host
        self.port = port
        self.storage_dir = Path(storage_dir).resolve()
        self.backlog = backlog
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._server_socket: Optional[socket.socket] = None

    @property
    def ready_event(self) -> threading.Event:
        return self._ready_event

    def stop(self) -> None:
        self._stop_event.set()
        if self._server_socket is not None:
            self._server_socket.close()

    def start(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(self.backlog)
            server.settimeout(1.0)
            self.port = int(server.getsockname()[1])
            self._server_socket = server
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                thread = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                thread.start()

    def _safe_file_path(self, filename: str) -> Optional[Path]:
        candidate = Path(filename)
        if candidate.name != filename or filename in {"", ".", ".."}:
            return None
        target = (self.storage_dir / filename).resolve()
        try:
            target.relative_to(self.storage_dir)
        except ValueError:
            return None
        return target

    def _handle_client(self, conn: socket.socket, _addr) -> None:
        with conn:
            conn.settimeout(30)
            try:
                request = recv_message(conn)
            except (ProtocolError, socket.timeout):
                return

            command = request.get("command")
            if command == "LIST":
                files = [
                    item.name
                    for item in self.storage_dir.iterdir()
                    if item.is_file()
                ]
                send_message(conn, {"status": "ok", "files": sorted(files)})
                return

            if command == "GET":
                filename = request.get("filename")
                if not isinstance(filename, str):
                    send_message(conn, {"status": "error", "message": "Missing filename"})
                    return

                file_path = self._safe_file_path(filename)
                if file_path is None:
                    send_message(conn, {"status": "error", "message": "Invalid filename"})
                    return

                if not file_path.exists() or not file_path.is_file():
                    send_message(conn, {"status": "error", "message": "File not found"})
                    return

                file_size = file_path.stat().st_size
                send_message(conn, {"status": "ok", "filename": filename, "size": file_size})
                with file_path.open("rb") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                return

            send_message(conn, {"status": "error", "message": "Unsupported command"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Central file server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--storage-dir", default="server_files")
    args = parser.parse_args()

    server = FileServer(args.host, args.port, args.storage_dir)
    try:
        print(f"Server listening on {args.host}:{args.port}, serving {server.storage_dir}")
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
