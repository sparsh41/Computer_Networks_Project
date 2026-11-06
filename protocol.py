import json
import struct
from typing import Any, Dict

_HEADER_SIZE = 4


class ProtocolError(Exception):
    pass


def recv_exact(sock, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ProtocolError("Connection closed while receiving data")
        data.extend(chunk)
    return bytes(data)


def send_message(sock, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)))
    sock.sendall(body)


def recv_message(sock) -> Dict[str, Any]:
    header = recv_exact(sock, _HEADER_SIZE)
    (size,) = struct.unpack("!I", header)
    body = recv_exact(sock, size)
    try:
        message = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProtocolError("Invalid JSON payload") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Payload must be a JSON object")
    return message
