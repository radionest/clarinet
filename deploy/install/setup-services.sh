#!/usr/bin/env bash
# Setup external services: PostgreSQL, RabbitMQ, Orthanc
# Runs on the target machine as root. Idempotent.
set -euo pipefail

DB_USER="${CLARINET_DB_USER:-clarinet}"
DB_NAME="${CLARINET_DB_NAME:-clarinet}"
DB_PASS="${CLARINET_DB_PASS:-$(openssl rand -hex 12)}"

RABBIT_USER="${CLARINET_RABBIT_USER:-clarinet}"
RABBIT_PASS="${CLARINET_RABBIT_PASS:-$(openssl rand -hex 12)}"

# logging.sh and provision.sh already sourced by caller (install-clarinet.sh)
init_logging "services"

# --- PostgreSQL ---
setup_postgresql() {
    log "Setting up PostgreSQL..."
    install_packages_if_missing postgresql postgresql-contrib

    systemctl enable --now postgresql

    # Create user if not exists
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}' CREATEDB;"
        log "PostgreSQL user '${DB_USER}' created"
    else
        sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASS}' CREATEDB;"
        log "PostgreSQL user '${DB_USER}' password updated"
    fi

    # Create database if not exists
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
        log "PostgreSQL database '${DB_NAME}' created"
    else
        log "PostgreSQL database '${DB_NAME}' already exists"
    fi

    # Export for generate-settings.sh
    export CLARINET_DB_PASS="$DB_PASS"
}

# --- RabbitMQ ---
setup_rabbitmq() {
    log "Setting up RabbitMQ..."
    install_packages_if_missing rabbitmq-server

    # Bind the AMQP listener to all interfaces so a remote worker VM (topology
    # mode) can reach the broker. The package default already listens on
    # 0.0.0.0:5672 — set it explicitly so remote access never depends on a
    # distro default.
    local rabbit_conf=/etc/rabbitmq/rabbitmq.conf
    local rabbit_conf_changed=0
    if ! grep -qs '^listeners\.tcp\.default' "$rabbit_conf"; then
        mkdir -p /etc/rabbitmq
        echo 'listeners.tcp.default = 0.0.0.0:5672' >> "$rabbit_conf"
        rabbit_conf_changed=1
        log "RabbitMQ listener bound to 0.0.0.0:5672"
    fi

    systemctl enable --now rabbitmq-server
    if [[ $rabbit_conf_changed -eq 1 ]]; then
        systemctl restart rabbitmq-server
        rabbitmqctl await_startup 2>/dev/null || true
    fi

    # Enable management plugin
    rabbitmq-plugins enable rabbitmq_management --quiet 2>/dev/null || true

    # Create user if not exists
    if ! rabbitmqctl list_users --formatter json 2>/dev/null | jq -e ".[] | select(.user==\"${RABBIT_USER}\")" &>/dev/null; then
        rabbitmqctl add_user "$RABBIT_USER" "$RABBIT_PASS"
        rabbitmqctl set_permissions -p / "$RABBIT_USER" ".*" ".*" ".*"
        rabbitmqctl set_user_tags "$RABBIT_USER" administrator
        log "RabbitMQ user '${RABBIT_USER}' created"
    else
        rabbitmqctl change_password "$RABBIT_USER" "$RABBIT_PASS"
        log "RabbitMQ user '${RABBIT_USER}' password updated"
    fi

    # Export for generate-settings.sh
    export CLARINET_RABBIT_PASS="$RABBIT_PASS"
}

# --- Orthanc PACS ---
setup_orthanc() {
    log "Setting up Orthanc PACS..."
    install_packages_if_missing orthanc

    # Enable remote REST API access (needed for test fixtures that query Orthanc from host)
    sed -i 's/"RemoteAccessAllowed"\s*:\s*false/"RemoteAccessAllowed" : true/' /etc/orthanc/orthanc.json
    # Accept any called AET — tests use WRONG_AET to verify permissive behavior
    sed -i 's/"DicomCheckCalledAet"\s*:\s*true/"DicomCheckCalledAet" : false/' /etc/orthanc/orthanc.json

    systemctl enable --now orthanc
    systemctl restart orthanc
    log "Orthanc running (DICOM: 4242, REST: 8042, remote API enabled)"
}

# --- Main ---
# Role gating: stand runs the DB + broker, pacs runs Orthanc, worker runs no
# local daemons (it reaches the broker + API + PACS over the network). Unset
# role ("all") keeps the single-VM behaviour where everything runs.
ROLE="${CLARINET_ROLE:-all}"
log "Installing external services (role: ${ROLE})..."
if [[ "$ROLE" == all || "$ROLE" == stand ]]; then
    setup_postgresql
    setup_rabbitmq
fi
if [[ "$ROLE" == all || "$ROLE" == pacs ]]; then
    setup_orthanc
fi
log "External services configured (role: ${ROLE})."
