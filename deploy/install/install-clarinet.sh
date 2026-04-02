#!/usr/bin/env bash
# Clarinet production installer
# Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]
# Runs on the target machine as root.
set -euo pipefail

WHEEL_PATH="${1:?Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]}"
DEPLOY_DIR="${2:?Usage: install-clarinet.sh <wheel_path> <deploy_dir>}"
SKIP_SERVICES="${3:-}"

PATH_PREFIX="${CLARINET_PATH_PREFIX:-/}"

source "${DEPLOY_DIR}/lib/logging.sh"
init_logging "install"
source "${DEPLOY_DIR}/lib/provision.sh"

VENV_DIR="${INSTALL_DIR}/venv"

# --- Step 1: System user (delegated to provision.sh) ---
setup_user() { setup_clarinet_user; }

# --- Step 2: Python 3.12 (delegated to provision.sh) ---
install_python() { install_python312; }

# --- Step 3: venv + wheel ---
install_wheel() {
    log "Installing Clarinet..."

    if [[ ! -d "$VENV_DIR" ]]; then
        python3.12 -m venv "$VENV_DIR"
    fi

    # Offline install if dependency wheels were shipped, otherwise fallback to PyPI
    local pip_extra_args=()
    if [[ -d "${DEPLOY_DIR}/deps" ]]; then
        log "Using offline dependency wheels from ${DEPLOY_DIR}/deps"
        pip_extra_args=(--no-index --find-links "${DEPLOY_DIR}/deps")
    else
        "$VENV_DIR/bin/pip" install --upgrade pip
    fi

    "$VENV_DIR/bin/pip" install "${pip_extra_args[@]}" "${WHEEL_PATH}[performance]"

    chown -R clarinet:clarinet "$INSTALL_DIR"
    log "Clarinet installed to $VENV_DIR"
}

# --- Step 5: External services ---
setup_services() {
    if [[ "$SKIP_SERVICES" == "--skip-services" ]]; then
        warn "Skipping external services setup"
        return
    fi
    source "${DEPLOY_DIR}/install/setup-services.sh"
    # Restore logging tag overwritten by sourced script
    init_logging "install"
}

# --- Step 6: Settings ---
generate_settings() {
    # Compute root_url from PATH_PREFIX (strip trailing slash for FastAPI root_path)
    # "/" → "" (root deployment), "/nir_liver/" → "/nir_liver"
    local root_url="${PATH_PREFIX%/}"
    export CLARINET_ROOT_URL="$root_url"
    bash "${DEPLOY_DIR}/install/generate-settings.sh"
}

# --- Step 7: Database init ---
init_database() {
    log "Initializing database..."
    # Link settings.toml so clarinet CLI finds it
    if [[ ! -f "${INSTALL_DIR}/settings.toml" ]]; then
        warn "settings.toml not found at ${INSTALL_DIR}/settings.toml"
        return
    fi

    cd "$INSTALL_DIR"
    sudo -u clarinet "$VENV_DIR/bin/clarinet" db init || warn "DB init returned non-zero (may already be initialized)"
    log "Database initialized"
}

# --- Step 8: OHIF Viewer ---
install_ohif() {
    log "Installing OHIF Viewer..."
    cd "$INSTALL_DIR"
    local ohif_args=(--force-config)
    # Use local tarball if shipped (VM may lack internet)
    local tarball
    tarball=$(find "${DEPLOY_DIR}" -maxdepth 1 -name 'ohif-app-*.tgz' -print -quit 2>/dev/null)
    if [[ -n "$tarball" ]]; then
        ohif_args+=(--from-file "$tarball")
    fi
    sudo -u clarinet "$VENV_DIR/bin/clarinet" ohif install "${ohif_args[@]}" || warn "OHIF install failed (non-critical)"
    log "OHIF Viewer installed"
}

# --- Step 9: Systemd units ---
install_systemd() {
    log "Installing systemd units..."
    cp "${DEPLOY_DIR}/systemd/clarinet-api.service" /etc/systemd/system/
    cp "${DEPLOY_DIR}/systemd/clarinet-worker@.service" /etc/systemd/system/

    systemctl daemon-reload
    systemctl enable clarinet-api
    systemctl enable clarinet-worker@default
    systemctl restart clarinet-api
    systemctl restart clarinet-worker@default
    log "Systemd services started"
}

# --- Step 9: Nginx ---
install_nginx() {
    log "Setting up nginx..."
    install_packages_if_missing nginx

    # Generate SSL cert
    bash "${DEPLOY_DIR}/nginx/generate-ssl.sh"

    # Render nginx config with path prefix
    local nginx_conf="${DEPLOY_DIR}/nginx/clarinet.conf"
    sed "s|__PATH_PREFIX__|${PATH_PREFIX}|g" "$nginx_conf" > /etc/nginx/sites-available/clarinet.conf
    ln -sf /etc/nginx/sites-available/clarinet.conf /etc/nginx/sites-enabled/clarinet.conf
    rm -f /etc/nginx/sites-enabled/default

    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
    log "Nginx configured"
}

# --- Step 10: Summary ---
print_summary() {
    local ip
    ip="$(hostname -I | awk '{print $1}')"
    echo ""
    echo "=============================================="
    echo " Clarinet Installation Complete"
    echo "=============================================="
    echo ""
    echo " URL:        https://${ip}${PATH_PREFIX}"
    echo " API:        https://${ip}${PATH_PREFIX}api/health"
    echo " Settings:   ${INSTALL_DIR}/settings.toml"
    echo " Data:       ${DATA_DIR}"
    echo " Logs:       ${LOG_DIR}"
    echo ""
    echo " Services:"
    echo "   systemctl status clarinet-api"
    echo "   systemctl status clarinet-worker@default"
    echo "   journalctl -u clarinet-api -f"
    echo ""
    echo "=============================================="
}

# --- Main ---
log "Starting Clarinet installation..."
log "Wheel: $WHEEL_PATH"
log "Deploy dir: $DEPLOY_DIR"

setup_user
install_python
install_wheel
setup_services
generate_settings
init_database
install_ohif
install_systemd
install_nginx
print_summary
