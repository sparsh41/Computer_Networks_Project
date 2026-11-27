import hashlib
import hmac
import json
import os
import re
import socket
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_HEADER = 1024 * 1024
CHUNK_SIZE = 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,180}$")

# Shared by every ClientHandler in this server process.
# Each entry is keyed by a unique transfer ID.
transfer_history = {}
transfer_history_lock = threading.Lock()

# Prevents two simultaneous uploads from selecting the same not-yet-created name.
_reserved_names = set()
_name_lock = threading.Lock()

EXTENSION_PREFIXES = {
    "TS": {
        ".txt", ".text", ".md", ".rtf", ".pdf", ".doc", ".docx",
        ".odt", ".csv", ".xls", ".xlsx", ".ppt", ".pptx", ".json",
        ".xml", ".html", ".htm", ".py", ".c", ".cpp", ".h", ".java",
    },
    "IM": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".ico"},
    "VD": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"},
    "AU": {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma", ".aiff"},
}


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


def file_prefix(filename):
    extension = Path(filename).suffix.lower()
    for prefix, extensions in EXTENSION_PREFIXES.items():
        if extension in extensions:
            return prefix
    return "OT"


def reserve_server_filename(storage, directory, original_filename):
    """Choose a unique prefixed name, checking disk and concurrent reservations."""
    prefix = file_prefix(original_filename)
    extension = Path(original_filename).suffix.lower()
    with _name_lock:
        number = 1
        while True:
            candidate_name = f"{prefix}{number:03d}{extension}"
            candidate = directory / candidate_name
            key = str(candidate.resolve())
            if not candidate.exists() and key not in _reserved_names:
                _reserved_names.add(key)
                return candidate, key
            number += 1


def release_server_filename(reservation_key):
    with _name_lock:
        _reserved_names.discard(reservation_key)


def record_transfer(action, username, original_filename, stored_filename,
                    relative_path, size, elapsed, speed):
    transfer_id = uuid.uuid4().hex
    record = {
        "transfer_id": transfer_id,
        "action": action,
        "user": username,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "server_path": relative_path,
        "size_bytes": size,
        "size_kb": round(size / 1024, 2),
        "duration_seconds": round(elapsed, 4),
        "speed_mb_s": round(speed, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with transfer_history_lock:
        transfer_history[transfer_id] = record
    return record


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
            active = self.monitor.client_connected()
            print(f"[CONNECTED] {self.username} from {self.address} | active clients: {active}")
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
            if self.username:
                active = self.monitor.client_disconnected()
                print(f"[DISCONNECTED] {self.username} | active clients: {active}")
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
                relative = req.get("path", ".")
                path = self.storage.resolve() if relative == "." else self.target(relative)
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
        requested_target = self.target(relative)
        original_filename = safe_name(requested_target.name)
        if requested_target == self.storage:
            raise ValueError("A filename is required")
        size = int(req["size"])
        if size < 0 or size > 20 * 1024**3:
            raise ValueError("File size is outside permitted range (0–20 GB)")

        requested_target.parent.mkdir(parents=True, exist_ok=True)
        target, reservation_key = reserve_server_filename(
            self.storage, requested_target.parent, original_filename
        )
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        start = time.perf_counter()
        self.monitor.mark_active(str(target))
        try:
            # Keep the existing client protocol: client waits for this before sending bytes.
            send_json(self.sock, {"ok": True, "ready": True,
                                  "filename": target.name,
                                  "original_filename": original_filename})
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
            speed = size / (1024 * 1024) / elapsed
            relative_stored = str(target.relative_to(self.storage))
            record = record_transfer("UPLOAD", self.username, original_filename,
                                     target.name, relative_stored, size, elapsed, speed)
            print("\n========== TRANSFER COMPLETE ==========")
            print(f"Action: UPLOAD | User: {self.username}")
            print(f"File: {original_filename} -> {target.name}")
            print(f"Size: {size / 1024:.2f} KB")
            print(f"Duration: {elapsed:.2f} seconds")
            print(f"Speed: {speed:.2f} MB/s")
            print("=======================================")
            send_json(self.sock, {"ok": True, "message": "Upload complete",
                                  "bytes": size, "seconds": elapsed, "mbps": speed,
                                  "filename": target.name,
                                  "original_filename": original_filename,
                                  "transfer_id": record["transfer_id"]})
        finally:
            temp.unlink(missing_ok=True)
            self.monitor.unmark_active(str(target))
            release_server_filename(reservation_key)

    def download(self, req):
        target = self.target(req["path"])
        if not target.is_file():
            raise FileNotFoundError("File does not exist")
        size = target.stat().st_size
        stored_filename = target.name
        start = time.perf_counter()
        self.monitor.mark_active(str(target))
        try:
            send_json(self.sock, {"ok": True, "size": size, "filename": stored_filename})
            with open(target, "rb") as source:
                while True:
                    block = source.read(CHUNK_SIZE)
                    if not block:
                        break
                    self.sock.sendall(block)
            elapsed = max(time.perf_counter() - start, 1e-9)
            speed = size / (1024 * 1024) / elapsed
            relative = str(target.relative_to(self.storage))
            record = record_transfer("DOWNLOAD", self.username, stored_filename,
                                     stored_filename, relative, size, elapsed, speed)
            print("\n========== TRANSFER COMPLETE ==========")
            print(f"Action: DOWNLOAD | User: {self.username}")
            print(f"File: {stored_filename}")
            print(f"Size: {size / 1024:.2f} KB")
            print(f"Duration: {elapsed:.2f} seconds")
            print(f"Speed: {speed:.2f} MB/s")
            print("=======================================")
        finally:
            self.monitor.unmark_active(str(target))

    def delete(self, req):
        target = self.target(req["path"])
        if self.monitor.is_active(str(target)):
            raise PermissionError("File is currently being processed")
        if not target.is_file():
            raise FileNotFoundError("File does not exist")
        target.unlink()
        send_json(self.sock, {"ok": True, "message": "File deleted"})