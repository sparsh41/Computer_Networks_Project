import argparse
import getpass
import hashlib
import json
import secrets
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Create/update a server user")
    parser.add_argument("username")
    parser.add_argument("--database", default="server/users.json")
    args = parser.parse_args()
    password = getpass.getpass("New password: ")
    if len(password) < 12:
        raise SystemExit("Use a password of at least 12 characters.")
    path = Path(args.database)
    path.parent.mkdir(parents=True, exist_ok=True)
    users = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 300_000).hex()
    users[args.username] = {"salt": salt.hex(), "password_hash": digest}
    path.write_text(json.dumps(users, indent=2), encoding="utf-8")
    print(f"User '{args.username}' saved to {path}. Keep this file private.")

if __name__ == "__main__":
    main()
