#!/usr/bin/env bash
# Bake a golden VM image with all system packages and services pre-installed.
# Runs on a temporary baking VM as root. The resulting image is exported as a
# standalone qcow2 that cmd_create() uses as a backing store.
# Usage: bake-image.sh [--dicom-dir /path/to/dicoms]
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DEPLOY_DIR/lib/logging.sh"
init_logging "bake"
source "$DEPLOY_DIR/lib/provision.sh"

VENV_DIR="${INSTALL_DIR}/venv"

DICOM_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dicom-dir) DICOM_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# --- System packages ---
install_system_packages() {
    log "Updating package lists..."
    apt-get update -qq

    log "Installing system packages..."
    install_packages_if_missing \
        curl git jq qemu-guest-agent cloud-guest-utils \
        nginx \
        postgresql postgresql-contrib \
        rabbitmq-server \
        orthanc

    install_python312

    log "All system packages installed"
}

# --- Enable services (so they auto-start on first boot) ---
enable_services() {
    log "Enabling services..."
    systemctl enable qemu-guest-agent
    systemctl enable postgresql
    systemctl enable rabbitmq-server
    systemctl enable orthanc
    # nginx stays disabled — configured per-VM during deploy
    systemctl disable nginx
    log "Services enabled"
}

# --- System user + directories (delegated to provision.sh) ---
setup_user() { setup_clarinet_user; }

# --- Python venv (no wheel — version-specific, installed at deploy time) ---
setup_venv() {
    log "Creating Python venv (without clarinet wheel)..."
    python3.12 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip > /dev/null
    chown -R clarinet:clarinet "$VENV_DIR"
    log "Venv ready at $VENV_DIR"
}

# --- Upload test DICOM images to Orthanc ---
upload_dicom() {
    if [[ -z "$DICOM_DIR" ]]; then
        log "No DICOM directory specified, skipping"
        return
    fi
    if [[ ! -d "$DICOM_DIR" ]]; then
        warn "DICOM directory not found: $DICOM_DIR"
        return
    fi

    # Ensure Orthanc is running
    systemctl start orthanc
    # Wait for Orthanc REST API to be ready
    local attempt=0
    while ! curl -sf http://localhost:8042/system > /dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge 30 ]]; then
            warn "Orthanc REST API not ready after 30s, skipping DICOM upload"
            return
        fi
        sleep 1
    done

    log "Uploading DICOM files from $DICOM_DIR..."
    local count=0
    local failed=0
    while IFS= read -r -d '' dcm_file; do
        if curl -sf -X POST http://localhost:8042/instances \
            --data-binary "@${dcm_file}" \
            -H "Content-Type: application/dicom" > /dev/null 2>&1; then
            count=$((count + 1))
        else
            failed=$((failed + 1))
        fi
    done < <(find "$DICOM_DIR" -type f -print0)

    log "DICOM upload complete: $count uploaded, $failed failed"
}

# --- Cleanup: make image reusable across VMs ---
cleanup_for_reuse() {
    log "Cleaning up for golden image reuse..."

    # Stop services before cleanup
    systemctl stop postgresql rabbitmq-server orthanc 2>/dev/null || true

    # Remove cloud-init state so it re-runs on next boot
    cloud-init clean --logs 2>/dev/null || rm -rf /var/lib/cloud/instances/* /var/lib/cloud/instance

    # Reset machine-id (regenerated on boot)
    truncate -s 0 /etc/machine-id
    rm -f /var/lib/dbus/machine-id

    # Remove SSH host keys (regenerated on boot by cloud-init or dpkg-reconfigure)
    rm -f /etc/ssh/ssh_host_*

    # Clear apt cache
    apt-get clean
    apt-get autoremove -y -qq > /dev/null

    # Clear logs
    find /var/log -type f -name "*.log" -delete 2>/dev/null || true
    find /var/log -type f -name "*.gz" -delete 2>/dev/null || true
    journalctl --rotate --vacuum-time=1s 2>/dev/null || true

    # Clear temp files
    rm -rf /tmp/* /var/tmp/*

    # Remove deploy staging (left over from bake scp)
    rm -rf /tmp/clarinet-deploy

    log "Cleanup complete"
}

# --- Main ---
log "Starting golden image bake..."

install_system_packages
enable_services
setup_user
setup_venv
upload_dicom
cleanup_for_reuse

log "Golden image bake complete!"
log "The VM should now be shut down and the overlay exported."
