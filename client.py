import argparse
import socket
from pathlib import Path
from typing import List

from protocol import ProtocolError, recv_exact, recv_message, send_message

CHUNK_SIZE = 64 * 1024


class FileTransferError(Exception):
    pass


class FileClient:
    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _open_connection(self) -> socket.socket:
        conn = socket.create_connection((self.host, self.port), timeout=self.timeout)
        conn.settimeout(self.timeout)
        return conn

    def list_files(self) -> List[str]:
        with self._open_connection() as conn:
            send_message(conn, {"command": "LIST"})
            response = recv_message(conn)

        if response.get("status") != "ok":
            raise FileTransferError(response.get("message", "Failed to list files"))

        files = response.get("files", [])
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise FileTransferError("Invalid response from server")
        return files

    def download_file(self, filename: str, destination: str) -> Path:
        destination_path = Path(destination)
        if destination_path.exists() and destination_path.is_dir():
            destination_path = destination_path / filename
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        with self._open_connection() as conn:
            send_message(conn, {"command": "GET", "filename": filename})
            response = recv_message(conn)

            if response.get("status") != "ok":
                raise FileTransferError(response.get("message", "Download failed"))

            size = response.get("size")
            if not isinstance(size, int) or size < 0:
                raise FileTransferError("Invalid file size from server")

            temp_path = destination_path.with_suffix(destination_path.suffix + ".part")
            with temp_path.open("wb") as output:
                remaining = size
                while remaining > 0:
                    chunk = recv_exact(conn, min(CHUNK_SIZE, remaining))
                    output.write(chunk)
                    remaining -= len(chunk)

        temp_path.replace(destination_path)
        return destination_path


def main() -> None:
    parser = argparse.ArgumentParser(description="File transfer client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List files available on server")

    get_parser = subparsers.add_parser("get", help="Download file from server")
    get_parser.add_argument("filename")
    get_parser.add_argument("destination")

    args = parser.parse_args()
    client = FileClient(args.host, args.port)

    try:
        if args.command == "list":
            files = client.list_files()
            for item in files:
                print(item)
        elif args.command == "get":
            path = client.download_file(args.filename, args.destination)
            print(f"Downloaded to {path}")
    except (FileTransferError, ProtocolError, OSError) as exc:
        raise SystemExit(f"Error: {exc}")


if __name__ == "__main__":
    main()
