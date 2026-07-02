#!/usr/bin/env bash
# Clarinet production installer
# Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]
# Runs on the target machine as root.
set -euo pipefail

WHEEL_PATH="${1:?Usage: install-clarinet.sh <wheel_path> <deploy_dir> [--skip-services]}"
DEPLOY_DIR="${2:?Usage: install-clarinet.sh <wheel_path> <deploy_dir>}"
SKIP_SERVICES="${3:-}"

PATH_PREFIX="${CLARINET_PATH_PREFIX:-/}"

# Role gating for multi-VM topologies (see deploy/vm/topologies/). Unset role
# means "all" — the single-VM path where every step runs, unchanged.
ROLE="${CLARINET_ROLE:-all}"
role_is() { [[ "$ROLE" == all || "$ROLE" == "$1" ]]; }

source "${DEPLOY_DIR}/lib/logging.sh"
init_logging "install"
source "${DEPLOY_DIR}/lib/provision.sh"

VENV_DIR="${INSTALL_DIR}/venv"

# Set by install_quarto when a Quarto toolchain is provisioned — gates the
# dedicated clarinet-worker@quarto instance in install_systemd.
QUARTO_INSTALLED=""

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

    # The quarto extra (jupyter/ipykernel/pandas) ships only when the deploy
    # bundle carries a Quarto tarball — see install_quarto below.
    local extras="performance"
    if [[ -n "$(find "${DEPLOY_DIR}" -maxdepth 1 -name 'quarto-*-linux-amd64.tar.gz' -print -quit 2>/dev/null)" ]]; then
        extras="performance,quarto"
    fi

    "$VENV_DIR/bin/pip" install "${pip_extra_args[@]}" "${WHEEL_PATH}[${extras}]"

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

# --- Step 5b: Downstream project (optional) ---
# The marker records that plan/ and review/ were placed by a project bundle —
# a later bare install removes exactly those (and only those) artifacts.
PROJECT_MARKER="${INSTALL_DIR}/.clarinet-project-bundle"

install_project() {
    local bundle="${CLARINET_PROJECT_BUNDLE:-}"
    if [[ -z "$bundle" ]]; then
        # Bare deployment: drop project artifacts left by a previous bundle
        # deploy (marker-gated — operator-managed files are not touched).
        if [[ -f "$PROJECT_MARKER" ]]; then
            log "Removing previous project bundle artifacts (plan/, review/)..."
            rm -rf "${INSTALL_DIR}/plan" "${INSTALL_DIR}/review" "$PROJECT_MARKER"
        fi
        return
    fi
    if [[ ! -d "$bundle/plan" || ! -f "$bundle/settings.toml" ]]; then
        err "Project bundle incomplete: $bundle (need plan/ and settings.toml)"
        exit 1
    fi

    log "Installing downstream project from $bundle..."
    # The DB keeps the existing admin's password hash, so snapshot the
    # currently effective admin_password before the base file is replaced —
    # generate-settings.sh honours CLARINET_ADMIN_PASSWORD first.
    if [[ -z "${CLARINET_ADMIN_PASSWORD:-}" ]]; then
        local existing_pass
        existing_pass=$(python3 -c "
import sys, tomllib, pathlib
m = {}
for name in ('settings.toml', 'settings.custom.toml'):
    f = pathlib.Path(sys.argv[1]) / name
    if f.is_file():
        m.update(tomllib.load(f.open('rb')))
print(m.get('admin_password', ''))
" "$INSTALL_DIR" 2>/dev/null) || existing_pass=""
        if [[ -n "$existing_pass" ]]; then
            export CLARINET_ADMIN_PASSWORD="$existing_pass"
        fi
    fi

    # Clean replace (not merge) so files dropped from the project disappear.
    rm -rf "${INSTALL_DIR}/plan" "${INSTALL_DIR}/review"
    cp -r "$bundle/plan" "${INSTALL_DIR}/plan"
    # The project's settings.toml becomes the base config; stand-specific
    # values are layered on top via settings.custom.toml (generate-settings.sh
    # overlay mode) — settings.custom.toml has higher priority in clarinet.
    # It may carry dev secrets — keep it group-readable only.
    cp "$bundle/settings.toml" "${INSTALL_DIR}/settings.toml"
    chmod 640 "${INSTALL_DIR}/settings.toml"
    if [[ -d "$bundle/review" ]]; then
        cp -r "$bundle/review" "${INSTALL_DIR}/review"
    fi
    touch "$PROJECT_MARKER"
    chown -R clarinet:clarinet "$INSTALL_DIR"
    export CLARINET_SETTINGS_OVERLAY=1
    log "Downstream project installed (plan/, settings.toml$([[ -d "$bundle/review" ]] && echo ', review/'))"
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
    if [[ ! -f "${INSTALL_DIR}/settings.toml" ]]; then
        warn "settings.toml not found at ${INSTALL_DIR}/settings.toml"
        return
    fi

    cd "$INSTALL_DIR"

    # Alembic init-migrations creates alembic.ini, env.py, script.py.mako
    # and generates the initial migration — idempotent, skips existing files
    sudo -u clarinet "$VENV_DIR/bin/clarinet" init-migrations || warn "init-migrations returned non-zero (may already be initialized)"

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

# --- Step 8b: Quarto CLI (optional — installed only when a tarball is shipped) ---
install_quarto() {
    local tarball
    tarball=$(find "${DEPLOY_DIR}" -maxdepth 1 -name 'quarto-*-linux-amd64.tar.gz' -print -quit 2>/dev/null)
    if [[ -z "$tarball" ]]; then
        log "No Quarto tarball in bundle — skipping (optional feature)"
        return
    fi
    # A bundled tarball means this host is meant to render Quarto reports — flag
    # it so install_systemd starts the dedicated worker that consumes the quarto
    # queue. Set on intent (not on install success): if the CLI install hiccups
    # the worker then fails renders loudly ("quarto binary not found") instead of
    # leaving them queued with no consumer forever.
    QUARTO_INSTALLED=1
    log "Installing Quarto CLI..."
    cd "$INSTALL_DIR"
    sudo -u clarinet "$VENV_DIR/bin/clarinet" quarto install --from-file "$tarball" || warn "Quarto install failed (non-critical)"
    log "Quarto CLI installed"
}

# --- Step 9: Systemd units ---
install_systemd() {
    log "Installing systemd units..."
    if role_is stand; then
        cp "${DEPLOY_DIR}/systemd/clarinet-api.service" /etc/systemd/system/
        cp "${DEPLOY_DIR}/systemd/clarinet-worker@.service" /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable clarinet-api
        systemctl enable clarinet-worker@default
        systemctl restart clarinet-api
        systemctl restart clarinet-worker@default

        # Quarto renders are bound to the quarto queue (render_quarto_report →
        # settings.quarto_queue_name) and run on a dedicated worker. The default
        # worker only consumes clarinet.<ns>.default, so without this instance the
        # quarto queue has no consumer and renders hang in "pending" forever.
        if [[ -n "$QUARTO_INSTALLED" ]]; then
            systemctl enable clarinet-worker@quarto
            systemctl restart clarinet-worker@quarto
            log "Quarto worker started (clarinet-worker@quarto)"
        else
            # Bare redeploy (no Quarto toolchain): tear down a worker left by a
            # previous Quarto-enabled deploy. A stale instance would keep a (now
            # version-mismatched) consumer registered and silently re-orphan renders.
            # --now also stops it; ignore failure when it was never enabled.
            systemctl disable --now clarinet-worker@quarto 2>/dev/null || true
        fi
        log "Systemd services started (api + worker@default)"
    else
        # worker role: install the unit template only. topology-wire enables the
        # per-queue instances once the broker/API overlay settings are in place.
        cp "${DEPLOY_DIR}/systemd/clarinet-worker@.service" /etc/systemd/system/
        systemctl daemon-reload
        log "Worker unit installed (enable deferred to topology-wire)"
    fi
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
    [[ -n "$QUARTO_INSTALLED" ]] && echo "   systemctl status clarinet-worker@quarto"
    echo "   journalctl -u clarinet-api -f"
    echo ""
    echo "=============================================="
}

# --- Main ---
log "Starting Clarinet installation (role: ${ROLE})..."
log "Wheel: $WHEEL_PATH"
log "Deploy dir: $DEPLOY_DIR"

# pacs runs Orthanc only (setup_services self-gates); stand + worker get the
# wheel/project/settings; the DB, OHIF/Quarto and nginx are stand-only; the
# worker installs only the systemd unit template (enabled later by wire).
if role_is stand || role_is worker; then
    setup_user
    install_python
    install_wheel
fi
setup_services
if role_is stand || role_is worker; then
    install_project
    generate_settings
fi
if role_is stand; then
    init_database
    install_ohif
    install_quarto
fi
if role_is stand || role_is worker; then
    install_systemd
fi
if role_is stand; then
    install_nginx
fi
print_summary
