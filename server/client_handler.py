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

# File extensions are used to classify uploads for server-side naming.
TYPE_PREFIXES = {
    "text": "TS",
    "image": "IM",
    "video": "VD",
    "audio": "AU",
}
TEXT_EXTENSIONS = {".txt", ".pdf", ".doc", ".docx", ".rtf", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"}

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
    if name in (".", "..") or "/" in name or "\\\\" in name:
        raise ValueError("Path separators are not allowed in names")
    return name

def classify_file(filename):
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text", "TS"
    if ext in IMAGE_EXTENSIONS:
        return "image", "IM"
    if ext in VIDEO_EXTENSIONS:
        return "video", "VD"
    if ext in AUDIO_EXTENSIONS:
        return "audio", "AU"
    return "other", "OT"

class ClientHandler:
    def __init__(self, raw_socket, address, storage, users_path, monitor, tls_context,
                 transfer_history=None, transfer_lock=None):
        self.raw_socket = raw_socket
        self.address = address
        self.storage = storage
        self.users_path = users_path
        self.monitor = monitor
        self.tls_context = tls_context
        self.transfer_history = transfer_history if transfer_history is not None else {}
        self.transfer_lock = transfer_lock if transfer_lock is not None else threading.Lock()
        self.sock = None
        self.username = None

    def record_transfer(self, action, original_filename, stored_filename, size, elapsed, speed):
        record = {
            "action": action,
            "user": self.username,
            "original_filename": original_filename,
            "stored_filename": stored_filename,
            "size_bytes": size,
            "size_kb": round(size / 1024, 2),
            "duration_seconds": round(elapsed, 2),
            "speed_mb_s": round(speed, 2),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        transfer_id = uuid.uuid4().hex