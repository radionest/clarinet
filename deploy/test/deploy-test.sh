#!/usr/bin/env bash
# Full E2E deployment test: create VM -> deploy -> smoke test -> cleanup
# Usage: deploy-test.sh
# Set KEEP_VM=true to skip cleanup after test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_SH="${SCRIPT_DIR}/../vm/vm.sh"

source "${SCRIPT_DIR}/../lib/logging.sh"
init_logging "e2e"

KEEP_VM="${KEEP_VM:-false}"

cleanup() {
    if [[ "$KEEP_VM" == "true" ]]; then
        warn "KEEP_VM=true — VM will not be destroyed"
        warn "Destroy manually with: bash $VM_SH destroy"
        return
    fi
    log "Cleaning up VM..."
    bash "$VM_SH" destroy || true
}

# Cleanup on exit (unless KEEP_VM)
trap cleanup EXIT

# Step 1: Create VM
log "Step 1/4: Creating VM..."
bash "$VM_SH" create

# Step 2: Deploy
log "Step 2/4: Deploying Clarinet..."
bash "$VM_SH" deploy

# Step 3: Wait for services to stabilize
log "Step 3/4: Waiting for services to start (15s)..."
sleep 15

# Step 4: Run smoke tests
log "Step 4/4: Running smoke tests..."
IP="$(bash "$VM_SH" ip)"
bash "${SCRIPT_DIR}/smoke-test.sh" "$IP"

log "E2E deployment test PASSED!"
