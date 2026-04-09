#!/usr/bin/env bash
# Run the test suite inside the Clarinet VM against PostgreSQL.
# Uses an SSH tunnel to reach VM's PostgreSQL from the host.
# Called by `make test-all-stages` (stages 7).
#
# Usage: vm-run-tests.sh [--skip-schema]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VM_SH="$PROJECT_DIR/deploy/vm/vm.sh"

source "$PROJECT_DIR/deploy/vm/vm.conf"
source "$PROJECT_DIR/deploy/lib/logging.sh"
init_logging "vm-tests"

SSH_OPTS=(
    -o StrictHostKeyChecking=no
    -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}"
    -i "$SSH_KEY_PATH"
)

LOCAL_PORT=15432
TEST_DB="clarinet_test"
TUNNEL_PID=""
VM_IP=""
SKIP_SCHEMA="${SKIP_SCHEMA:-0}"

# Parse args
for arg in "$@"; do
    case "$arg" in
        --skip-schema) SKIP_SCHEMA=1 ;;
    esac
done

cleanup() {
    log "Cleaning up..."
    if [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        wait "$TUNNEL_PID" 2>/dev/null || true
        log "SSH tunnel closed."
    fi
    if [ -n "$VM_IP" ]; then
        # shellcheck disable=SC2029
        ssh "${SSH_OPTS[@]}" "clarinet@$VM_IP" \
            "sudo -u postgres dropdb --if-exists $TEST_DB" 2>/dev/null || true
        log "Test database dropped."
    fi
}

# Get VM IP
VM_IP=$(bash "$VM_SH" ip 2>/dev/null)
if [ -z "$VM_IP" ]; then
    err "Cannot determine VM IP. Is the VM running?"
    exit 1
fi
log "VM IP: $VM_IP"

# Read DB password from VM settings
DB_PASS=$(ssh "${SSH_OPTS[@]}" "clarinet@$VM_IP" \
    "python3 -c \"import tomllib; print(tomllib.load(open('/opt/clarinet/settings.toml','rb'))['database_password'])\"")
if [ -z "$DB_PASS" ]; then
    err "Cannot read database_password from VM settings.toml"
    exit 1
fi

# Create test database on VM
log "Creating test database '$TEST_DB' on VM..."
# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "clarinet@$VM_IP" \
    "sudo -u postgres dropdb --if-exists $TEST_DB; sudo -u postgres createdb --owner=clarinet $TEST_DB"

trap cleanup EXIT

# Open SSH tunnel
log "Opening SSH tunnel (localhost:$LOCAL_PORT -> VM:5432)..."
ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_PORT:localhost:5432" "clarinet@$VM_IP" &
TUNNEL_PID=$!

# A live `ssh -N` process is not proof the tunnel works: OpenSSH silently
# disables port forwarding when the host key conflicts (e.g. stale
# known_hosts after VM reimage), leaving `kill -0` happy but no listener on
# the local port. Probe the listener directly with a short retry loop.
tunnel_ready=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if (exec 3<>"/dev/tcp/localhost/$LOCAL_PORT") 2>/dev/null; then
        exec 3<&-
        exec 3>&-
        tunnel_ready=1
        break
    fi
    sleep 0.5
done
if [ "$tunnel_ready" -ne 1 ]; then
    err "SSH tunnel did not open a listener on localhost:$LOCAL_PORT"
    err "Check for stale known_hosts entry for $VM_IP in $KNOWN_HOSTS_FILE"
    exit 1
fi
log "Tunnel active (PID $TUNNEL_PID)."

export CLARINET_TEST_DATABASE_URL="postgresql+asyncpg://clarinet:${DB_PASS}@localhost:${LOCAL_PORT}/${TEST_DB}"

cd "$PROJECT_DIR"

log "=== Stage 7a: unit tests (PostgreSQL) ==="
uv run pytest tests/ \
    -m "not pipeline and not dicom and not slicer and not schema" \
    -n auto --dist loadgroup -q

log "=== Stage 7b: test-fast (PostgreSQL) ==="
# slicer tests excluded: they hit host-local 3D Slicer sequentially in Stage 5b,
# parallel xdist would race against the single-threaded Slicer web server.
uv run pytest tests/ \
    -m "not slicer and not schema" \
    -n auto --dist loadgroup -q

if [ "$SKIP_SCHEMA" != "1" ]; then
    log "=== Stage 7c: schema tests (PostgreSQL) ==="
    uv run pytest tests/schema/ -m schema --no-header -q
else
    warn "Schema tests skipped (SKIP_SCHEMA=1)"
fi

log "All VM PostgreSQL tests passed!"
