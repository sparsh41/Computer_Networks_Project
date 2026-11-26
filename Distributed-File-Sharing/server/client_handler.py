import hashlib
import hmac
import json
import mimetypes
import os
import re
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

MAX_HEADER = 1024 * 1024
CHUNK_SIZE = 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,180}$")

def recv_exact(sock, count):
    chunks = []
    remaining = count
    while remaining:
        data = sock.recv(min(remaining, CHUNK_SIZE))
        if not data:
            raise ConnectionError("Client disconnected during transfer")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)

def recv_json(sock):
    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if size <= 0 or size > MAX_HEADER:
        raise ValueError("Invalid protocol header length")
    return json.loads(recv_exact(sock, size).decode("utf-8"))

def send_json(sock, payload):
    encoded = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack("!I", len(encoded)) + encoded)

def safe_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("Invalid filename or directory name")
    if name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("Path separators are not allowed in names")
    return name

class ClientHandler:
    def __init__(self, raw_socket, address, storage, users_path, monitor, tls_context):
        self.raw_socket = raw_socket
        self.address = address
        self.storage = storage
        self.users_path = users_path
        self.monitor = monitor
        self.tls_context = tls_context
        self.sock = None
        self.username = None

    def run(self):
        started = time.perf_counter()
        try:
            self.sock = (self.tls_context.wrap_socket(self.raw_socket, server_side=True)
                         if self.tls_context else self.raw_socket)
            self.sock.settimeout(600)
            if not self.authenticate():
                return
            send_json(self.sock, {"ok": True, "message": "Authentication successful"})
            while True:
                request = recv_json(self.sock)
                command = request.get("command")
                if command == "exit":
                    send_json(self.sock, {"ok": True, "message": "Goodbye"})
                    break
                self.handle(command, request)
        except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{self.address}: connection ended: {exc}")
        except Exception as exc:
            print(f"{self.address}: unexpected error: {exc}")
            try:
                send_json(self.sock, {"ok": False, "error": "Internal server error"})
            except Exception:
                pass
        finally:
            try:
                (self.sock or self.raw_socket).close()
            except OSError:
                pass
            print(f"Disconnected {self.address} ({time.perf_counter()-started:.2f}s session)")

    def authenticate(self):
        users = json.loads(self.users_path.read_text(encoding="utf-8"))
        send_json(self.sock, {"ok": True, "message": "login_required"})
        login = recv_json(self.sock)
        username, password = login.get("username"), login.get("password")
        record = users.get(username) if isinstance(username, str) else None
        if not record or not isinstance(password, str):
            send_json(self.sock, {"ok": False, "error": "Invalid username or password"})
            return False
        salt = bytes.fromhex(record["salt"])
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 300_000).hex()
        if not hmac.compare_digest(candidate, record["password_hash"]):
            send_json(self.sock, {"ok": False, "error": "Invalid username or password"})
            return False
        self.username = username
        return True

    def target(self, relative):
        if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
            raise ValueError("A relative server path is required")
        parts = Path(relative).parts
        if any(part in ("..", ".") for part in parts):
            raise ValueError("Path traversal is not allowed")
        target = (self.storage / relative).resolve()
        if target != self.storage and self.storage not in target.parents:
            raise ValueError("Path is outside server storage")
        return target

    def handle(self, command, req):
        try:
            if command == "dir":
                path = self.target(req.get("path", "."))
                if not path.exists() or not path.is_dir():
                    raise FileNotFoundError("Directory does not exist")
                entries = []
                for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file",
                                    "size": item.stat().st_size if item.is_file() else None})
                send_json(self.sock, {"ok": True, "entries": entries})
            elif command == "mkdir":
                path = self.target(req["path"])
                path.mkdir(parents=True, exist_ok=False)
                send_json(self.sock, {"ok": True, "message": "Directory created"})
            elif command == "rmdir":
                path = self.target(req["path"])
                if path == self.storage:
                    raise ValueError("Cannot delete storage root")
                if not path.is_dir():
                    raise FileNotFoundError("Directory does not exist")
                path.rmdir()
                send_json(self.sock, {"ok": True, "message": "Directory deleted"})
            elif command == "upload":
                self.upload(req)
            elif command == "download":
                self.download(req)
            elif command == "delete":
                self.delete(req)
            else:
                raise ValueError("Unknown command")
        except FileExistsError as exc:
            send_json(self.sock, {"ok": False, "error": str(exc), "code": "EXISTS"})
        except FileNotFoundError as exc:
            send_json(self.sock, {"ok": False, "error": str(exc), "code": "NOT_FOUND"})
        except (ValueError, KeyError, PermissionError, IsADirectoryError, OSError) as exc:
            send_json(self.sock, {"ok": False, "error": str(exc)})

    def upload(self, req):
        relative = req["path"]
        target = self.target(relative)
        filename = safe_name(target.name)
        if target == self.storage:
            raise ValueError("A filename is required")
        size = int(req["size"])
        if size < 0 or size > 20 * 1024**3:
            raise ValueError("File size is outside permitted range (0–20 GB)")
        if target.exists() and not req.get("overwrite", False):
            send_json(self.sock, {"ok": False, "code": "EXISTS", "error": "File already exists"})
            return
        if target.exists() and not target.is_file():
            raise IsADirectoryError("The destination exists and is not a regular file")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        # Tell the client it is safe to begin streaming the announced byte count.
        send_json(self.sock, {"ok": True, "ready": True})
        start = time.perf_counter()
        self.monitor.mark_active(str(target))
        try:
            remaining = size
            with open(temp, "wb") as output:
                while remaining:
                    block = self.sock.recv(min(CHUNK_SIZE, remaining))
                    if not block:
                        raise ConnectionError("Upload interrupted")
                    output.write(block)
                    remaining -= len(block)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, target)
            elapsed = max(time.perf_counter() - start, 1e-9)
            self.monitor.record("upload", relative, size, elapsed, self.username)
            send_json(self.sock, {"ok": True, "message": "Upload complete",
                                  "bytes": size, "seconds": elapsed,
                                  "mbps": size / (1024*1024) / elapsed})
        finally:
            temp.unlink(missing_ok=True)
            self.monitor.unmark_active(str(target))

    def download(self, req):
        target = self.target(req["path"])
        if not target.is_file():
            raise FileNotFoundError("File does not exist")
        size = target.stat().st_size
        start = time.perf_counter()
        self.monitor.mark_active(str(target))
        try:
            send_json(self.sock, {"ok": True, "size": size, "filename": target.name})
            with open(target, "rb") as source:
                while True:
                    block = source.read(CHUNK_SIZE)
                    if not block:
                        break
                    self.sock.sendall(block)
            elapsed = max(time.perf_counter() - start, 1e-9)
            self.monitor.record("download", str(target.relative_to(self.storage)), size,
                                elapsed, self.username)
        finally:
            self.monitor.unmark_active(str(target))

    def delete(self, req):
        target = self.target(req["path"])
        if str(target) in self.monitor.active_files:
            raise PermissionError("File is currently being processed")
        if not target.is_file():
            raise FileNotFoundError("File does not exist")
        target.unlink()
        send_json(self.sock, {"ok": True, "message": "File deleted"})
