#!/usr/bin/env bash
# Run the full test suite against PostgreSQL on the Clarinet VM.
# Opens an SSH tunnel, creates a temporary test database, runs pytest, cleans up.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VM_SH="$PROJECT_DIR/deploy/vm/vm.sh"

LOCAL_PORT=15432
TEST_DB="clarinet_test"
TUNNEL_PID=""

cleanup() {
    echo "Cleaning up..."
    if [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        wait "$TUNNEL_PID" 2>/dev/null || true
        echo "SSH tunnel closed."
    fi
    # Drop test database (ignore errors if it doesn't exist)
    ssh -o StrictHostKeyChecking=no "clarinet@$VM_IP" \
        "sudo -u postgres dropdb --if-exists $TEST_DB" 2>/dev/null || true
    echo "Test database dropped."
}

# Get VM IP
VM_IP=$(bash "$VM_SH" ip 2>/dev/null)
if [ -z "$VM_IP" ]; then
    echo "Error: cannot determine VM IP. Is the VM running?" >&2
    exit 1
fi
echo "VM IP: $VM_IP"

# Read DB password from VM settings
DB_PASS=$(ssh -o StrictHostKeyChecking=no "clarinet@$VM_IP" \
    "grep '^database_password' /opt/clarinet/settings.toml | head -1 | sed 's/.*= *\"//;s/\".*//'")
if [ -z "$DB_PASS" ]; then
    echo "Error: cannot read database_password from VM settings.toml" >&2
    exit 1
fi

# Create test database on VM
echo "Creating test database '$TEST_DB' on VM..."
ssh -o StrictHostKeyChecking=no "clarinet@$VM_IP" \
    "sudo -u postgres createdb --owner=clarinet $TEST_DB" 2>/dev/null || true

trap cleanup EXIT

# Open SSH tunnel
echo "Opening SSH tunnel (localhost:$LOCAL_PORT -> VM:5432)..."
ssh -o StrictHostKeyChecking=no -N -L "$LOCAL_PORT:localhost:5432" "clarinet@$VM_IP" &
TUNNEL_PID=$!
sleep 1

if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "Error: SSH tunnel failed to start" >&2
    exit 1
fi
echo "Tunnel active (PID $TUNNEL_PID)."

# Run tests
export CLARINET_TEST_DATABASE_URL="postgresql+asyncpg://clarinet:${DB_PASS}@localhost:${LOCAL_PORT}/${TEST_DB}"

echo ""
echo "Running tests against PostgreSQL..."
echo "URL: postgresql+asyncpg://clarinet:***@localhost:${LOCAL_PORT}/${TEST_DB}"
echo ""

cd "$PROJECT_DIR"
uv run pytest tests/ \
    -m "not pipeline and not dicom and not slicer and not schema" \
    -q \
    "$@"
