import argparse
import getpass
import json
import socket
import ssl
import struct
import sys
import time
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
MAX_HEADER = 1024 * 1024

def recv_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(min(CHUNK_SIZE, count-len(data)))
        if not chunk:
            raise ConnectionError("Server disconnected")
        data.extend(chunk)
    return bytes(data)

def recv_json(sock):
    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if size <= 0 or size > MAX_HEADER:
        raise ValueError("Invalid server response")
    return json.loads(recv_exact(sock, size).decode("utf-8"))

def send_json(sock, payload):
    data = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)

def human_size(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024

class FileClient:
    def __init__(self, host, port, insecure=False, ca=None):
        raw = socket.create_connection((host, port), timeout=20)
        if insecure:
            self.sock = raw
            print("WARNING: TLS disabled; credentials are exposed to network observers.")
        else:
            context = ssl.create_default_context(cafile=ca)
            self.sock = context.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(600)

    def login(self):
        recv_json(self.sock)
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        send_json(self.sock, {"username": username, "password": password})
        response = recv_json(self.sock)
        if not response.get("ok"):
            raise PermissionError(response.get("error", "Login failed"))
        response = recv_json(self.sock)
        print(response.get("message", "Connected"))

    def request(self, payload):
        send_json(self.sock, payload)
        return recv_json(self.sock)

    def upload(self, local, remote):
        path = Path(local)
        if not path.is_file():
            print("Local file does not exist.")
            return
        size = path.stat().st_size
        response = self.request({"command": "upload", "path": remote, "size": size})
        if response.get("code") == "EXISTS":
            answer = input("File exists on server. Overwrite? (y/N): ").strip().lower()
            if answer != "y":
                print("Upload cancelled.")
                return
            response = self.request({"command": "upload", "path": remote,
                                     "size": size, "overwrite": True})
        if not response.get("ok"):
            print("Upload failed:", response.get("error"))
            return
        start = time.perf_counter()
        sent = 0
        with path.open("rb") as source:
            while True:
                block = source.read(CHUNK_SIZE)
                if not block:
                    break
                self.sock.sendall(block)
                sent += len(block)
                print(f"\rUploading: {sent/size*100 if size else 100:.1f}%", end="")
        result = recv_json(self.sock)
        print()
        if result.get("ok"):
            print(f"Upload complete: {human_size(size)} in {result['seconds']:.2f}s "
                  f"({result['mbps']:.2f} MB/s)")
        else:
            print("Upload failed:", result.get("error"))

    def download(self, remote, local=None):
        response = self.request({"command": "download", "path": remote})
        if not response.get("ok"):
            print("Download failed:", response.get("error"))
            return
        size = response["size"]
        destination = Path(local or Path(remote).name)
        temp = destination.with_name(destination.name + ".part")
        start = time.perf_counter()
        received = 0
        try:
            with temp.open("wb") as output:
                while received < size:
                    block = self.sock.recv(min(CHUNK_SIZE, size-received))
                    if not block:
                        raise ConnectionError("Download interrupted")
                    output.write(block)
                    received += len(block)
                    print(f"\rDownloading: {received/size*100 if size else 100:.1f}%", end="")
            temp.replace(destination)
            elapsed = max(time.perf_counter()-start, 1e-9)
            print(f"\nDownload complete: {human_size(size)} in {elapsed:.2f}s "
                  f"({size/1024/1024/elapsed:.2f} MB/s)")
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    def run_command(self, line):
        parts = line.strip().split()
        if not parts:
            return
        cmd = parts[0].lower()
        if cmd == "upload" and len(parts) in (2, 3):
            self.upload(parts[1], parts[2] if len(parts) == 3 else Path(parts[1]).name)
        elif cmd == "download" and len(parts) in (2, 3):
            self.download(parts[1], parts[2] if len(parts) == 3 else None)
        elif cmd == "dir" and len(parts) <= 2:
            response = self.request({"command": "dir", "path": parts[1] if len(parts)==2 else "."})
            if response.get("ok"):
                for item in response["entries"]:
                    suffix = "/" if item["type"] == "directory" else ""
                    size = "" if item["size"] is None else f"  {human_size(item['size'])}"
                    print(item["name"] + suffix + size)
            else:
                print(response.get("error"))
        elif cmd == "delete" and len(parts) == 2:
            response = self.request({"command": "delete", "path": parts[1]})
            print(response.get("message") if response.get("ok") else response.get("error"))
        elif cmd == "subfolder" and len(parts) == 3 and parts[1].lower() in ("create", "delete"):
            action = "mkdir" if parts[1].lower() == "create" else "rmdir"
            response = self.request({"command": action, "path": parts[2]})
            print(response.get("message") if response.get("ok") else response.get("error"))
        elif cmd in ("help", "?"):
            print("Commands: dir [path], upload LOCAL [REMOTE], download REMOTE [LOCAL], "
                  "delete REMOTE, subfolder create PATH, subfolder delete PATH, exit")
        elif cmd == "exit":
            self.request({"command": "exit"})
            return False
        else:
            print("Invalid command. Type help.")
        return True

    def close(self):
        self.sock.close()

def main():
    parser = argparse.ArgumentParser(description="File-sharing client")
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--insecure", action="store_true",
                        help="Development only: connect without TLS")
    parser.add_argument("--ca", help="CA certificate used to verify the server")
    args = parser.parse_args()
    client = FileClient(args.host, args.port, args.insecure, args.ca)
    try:
        client.login()
        print("Type help for commands.")
        while True:
            try:
                if client.run_command(input("Server> ")) is False:
                    break
            except (OSError, ValueError, ConnectionError) as exc:
                print("Operation failed:", exc)
                break
    finally:
        client.close()

if __name__ == "__main__":
    main()
