#!/usr/bin/env bash
# Clarinet production installer
# Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]
# Runs on the target machine as root.
set -euo pipefail

WHEEL_PATH="${1:?Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]}"
DEPLOY_DIR="${2:?Usage: install-clarinet.sh <wheel_path> <deploy_dir>}"
SKIP_SERVICES="${3:-}"

INSTALL_DIR="/opt/clarinet"
VENV_DIR="${INSTALL_DIR}/venv"
DATA_DIR="/var/lib/clarinet/data"
LOG_DIR="/var/log/clarinet"

PATH_PREFIX="${CLARINET_PATH_PREFIX:-/}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
log()  { echo -e "${GREEN}[install]${NC} $*"; }
warn() { echo -e "${YELLOW}[install]${NC} $*"; }

# --- Step 1: System user ---
setup_user() {
    if id clarinet &>/dev/null; then
        log "System user 'clarinet' already exists"
    else
        useradd --system --home-dir "$INSTALL_DIR" --create-home --shell /bin/bash clarinet
        log "Created system user 'clarinet'"
    fi
    mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"
    chown -R clarinet:clarinet "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"
}

# --- Step 2: Python 3.12 ---
install_python() {
    if python3.12 --version &>/dev/null; then
        log "Python 3.12 already installed"
    else
        log "Installing Python 3.12 via deadsnakes PPA..."
        apt-get update -qq
        apt-get install -y -qq software-properties-common > /dev/null
        add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
        apt-get update -qq
        apt-get install -y -qq python3.12 > /dev/null
        log "Python 3.12 installed"
    fi

    # Ensure venv and dev packages are present (cloud images often ship
    # python3.12 without python3.12-venv)
    if ! dpkg -s python3.12-venv &>/dev/null; then
        log "Installing python3.12-venv..."
        apt-get update -qq
        apt-get install -y -qq python3.12-venv python3.12-dev > /dev/null
    fi
}

# --- Step 3: venv + wheel ---
install_wheel() {
    log "Creating venv and installing Clarinet..."

    python3.12 -m venv --clear "$VENV_DIR"

    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install "${WHEEL_PATH}[pipeline,performance,dicom]"

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
    # Restore log/warn overwritten by sourced script
    log()  { echo -e "${GREEN}[install]${NC} $*"; }
    warn() { echo -e "${YELLOW}[install]${NC} $*"; }
}

# --- Step 6: Settings ---
generate_settings() {
    # Compute root_url from PATH_PREFIX (strip trailing slash for FastAPI root_path)
    local root_url="$PATH_PREFIX"
    if [[ "$root_url" != "/" ]]; then
        root_url="${root_url%/}"  # strip trailing slash
    fi
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

# --- Step 8: Systemd units ---
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
    apt-get install -y -qq nginx > /dev/null

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
install_systemd
install_nginx
print_summary
