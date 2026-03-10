#!/usr/bin/env bash
# Generate self-signed TLS certificate for Clarinet
# Idempotent — skips if cert already exists
set -euo pipefail

CERT_PATH="/etc/ssl/certs/clarinet-selfsigned.crt"
KEY_PATH="/etc/ssl/private/clarinet-selfsigned.key"

GREEN='\033[0;32m'
NC='\033[0m'
log() { echo -e "${GREEN}[ssl]${NC} $*"; }

if [[ -f "$CERT_PATH" && -f "$KEY_PATH" ]]; then
    log "TLS certificate already exists, skipping"
    exit 0
fi

log "Generating self-signed TLS certificate..."

openssl req -x509 -nodes \
    -days 365 \
    -newkey rsa:2048 \
    -keyout "$KEY_PATH" \
    -out "$CERT_PATH" \
    -subj "/CN=clarinet/O=Clarinet/C=RU"

chmod 600 "$KEY_PATH"

log "Certificate generated:"
log "  Cert: $CERT_PATH"
log "  Key:  $KEY_PATH"
