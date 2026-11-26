import argparse
import json
import os
import socket
import ssl
import threading
from pathlib import Path

from client_handler import ClientHandler
from performance_monitor import PerformanceMonitor

def main():
    parser = argparse.ArgumentParser(description="Distributed file-sharing server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--storage", default="storage")
    parser.add_argument("--users", default="users.json")
    parser.add_argument("--cert", default="certs/server.crt")
    parser.add_argument("--key", default="certs/server.key")
    parser.add_argument("--no-tls", action="store_true",
                        help="Development only; passwords are NOT protected in transit.")
    args = parser.parse_args()

    storage = Path(args.storage).resolve()
    for folder in ("text", "audio", "video", "other"):
        (storage / folder).mkdir(parents=True, exist_ok=True)
    users_path = Path(args.users).resolve()
    if not users_path.exists():
        users_path.write_text("{}", encoding="utf-8")
        print(f"Created empty user database at {users_path}. Add users with tools/create_user.py")

    monitor = PerformanceMonitor(Path("logs"))
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((args.host, args.port))
    server_socket.listen(100)

    if args.no_tls:
        tls_context = None
        print("WARNING: TLS DISABLED. Use only on an isolated development network.")
    else:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(args.cert, args.key)

    print(f"Listening on {args.host}:{args.port}; storage={storage}")
    try:
        while True:
            raw_socket, address = server_socket.accept()
            thread = threading.Thread(
                target=ClientHandler(raw_socket, address, storage, users_path, monitor,
                                     tls_context).run,
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server_socket.close()
        monitor.close()

if __name__ == "__main__":
    main()
