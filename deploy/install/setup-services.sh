#!/usr/bin/env bash
# Setup external services: PostgreSQL, RabbitMQ, Orthanc
# Runs on the target machine as root. Idempotent.
set -euo pipefail

DB_USER="${CLARINET_DB_USER:-clarinet}"
DB_NAME="${CLARINET_DB_NAME:-clarinet}"
DB_PASS="${CLARINET_DB_PASS:-$(openssl rand -base64 16)}"

RABBIT_USER="${CLARINET_RABBIT_USER:-clarinet}"
RABBIT_PASS="${CLARINET_RABBIT_PASS:-$(openssl rand -base64 16)}"

# logging.sh already sourced by caller (install-clarinet.sh)
init_logging "services"

# --- PostgreSQL ---
setup_postgresql() {
    log "Setting up PostgreSQL..."
    if ! dpkg -s postgresql &>/dev/null; then
        apt-get install -y -qq postgresql postgresql-contrib > /dev/null
    fi

    systemctl enable --now postgresql

    # Create user if not exists
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
        log "PostgreSQL user '${DB_USER}' created"
    else
        sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
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
    if ! dpkg -s rabbitmq-server &>/dev/null; then
        apt-get install -y -qq rabbitmq-server > /dev/null
    fi

    systemctl enable --now rabbitmq-server

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
    if ! dpkg -s orthanc &>/dev/null; then
        apt-get install -y -qq orthanc > /dev/null
    fi

    systemctl enable --now orthanc
    log "Orthanc running (DICOM: 4242, REST: 8042)"
}

# --- Main ---
log "Installing external services..."
setup_postgresql
setup_rabbitmq
setup_orthanc
log "All services configured."
