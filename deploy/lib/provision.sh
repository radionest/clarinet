#!/usr/bin/env bash
# Shared provisioning helpers — used by both bake-image.sh and install-clarinet.sh.
# Requires logging.sh to be sourced first.

INSTALL_DIR="/opt/clarinet"
DATA_DIR="/var/lib/clarinet/data"
LOG_DIR="/var/log/clarinet"

# install_packages_if_missing pkg1 pkg2 ... — idempotent apt install
install_packages_if_missing() {
    local missing=()
    for pkg in "$@"; do
        dpkg -s "$pkg" &>/dev/null || missing+=("$pkg")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "Installing ${missing[*]}..."
        apt-get install -y -qq "${missing[@]}" > /dev/null
    fi
}

# setup_clarinet_user — create system user + standard directories
setup_clarinet_user() {
    if id clarinet &>/dev/null; then
        log "System user 'clarinet' already exists"
    else
        useradd --system --home-dir "$INSTALL_DIR" --create-home --shell /bin/bash clarinet
        log "Created system user 'clarinet'"
    fi
    mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"
    chown -R clarinet:clarinet "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"
}

# install_python312 — idempotent Python 3.12 + venv + dev via deadsnakes PPA
install_python312() {
    if ! python3.12 --version &>/dev/null; then
        log "Installing Python 3.12 via deadsnakes PPA..."
        install_packages_if_missing software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
        apt-get update -qq
    fi
    install_packages_if_missing python3.12 python3.12-venv python3.12-dev
}
