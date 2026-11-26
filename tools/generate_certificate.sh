#!/usr/bin/env bash
set -euo pipefail
mkdir -p server/certs
# For a classroom/lab deployment, create a self-signed certificate.
# Replace the SAN with the server DNS name or IP clients will use.
openssl req -x509 -newkey rsa:3072 -keyout server/certs/server.key \
  -out server/certs/server.crt -days 365 -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 server/certs/server.key
echo "Created server/certs/server.crt and server/certs/server.key"
echo "For remote clients, regenerate with the server's DNS name/IP in subjectAltName."
