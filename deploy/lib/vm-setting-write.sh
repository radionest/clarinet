#!/usr/bin/env bash
# Upsert one or more effective clarinet settings on a deployed VM, honouring the
# same layering clarinet itself uses: writes go to settings.custom.toml (the
# stand overlay), which wins over settings.toml. The write counterpart of
# vm-setting.sh — ships settings_overlay.py over stdin so the host stays the
# single source of truth for the merge logic.
#
# Usage: vm-setting-write.sh <vm_ip> KEY=VALUE [KEY=VALUE ...]
# Self-contained: sources vm.conf for SSH_KEY_PATH/KNOWN_HOSTS_FILE unless the
# caller already exported them (plain `. vm.conf` does not export).
set -euo pipefail

IP="${1:?usage: vm-setting-write.sh <vm_ip> KEY=VALUE ...}"
shift
[[ $# -gt 0 ]] || { echo "vm-setting-write.sh: no KEY=VALUE pairs given" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${SSH_KEY_PATH:-}" ]]; then
    # shellcheck source=../vm/vm.conf
    source "${SCRIPT_DIR}/../vm/vm.conf"
fi

REMOTE_FILE="/opt/clarinet/settings.custom.toml"

# Validate keys (the VALUE half may hold IPs/URLs/secrets) and single-quote each
# pair for the remote shell.
quoted_pairs=()
for pair in "$@"; do
    key="${pair%%=*}"
    if [[ "$pair" != *=* || ! "$key" =~ ^[A-Za-z0-9_]+$ ]]; then
        echo "vm-setting-write.sh: invalid pair '$pair' (need KEY=VALUE, identifier key)" >&2
        exit 2
    fi
    # End the quote, splice an escaped ', resume — values may contain '.
    escaped_pair="${pair//\'/\'\"\'\"\'}"
    quoted_pairs+=("'${escaped_pair}'")
done

exec ssh -o StrictHostKeyChecking=no \
    -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}" \
    -i "${SSH_KEY_PATH:?vm.conf not sourced (SSH_KEY_PATH unset)}" \
    "clarinet@${IP}" \
    "sudo python3 - '${REMOTE_FILE}' ${quoted_pairs[*]} && \
     sudo chown clarinet:clarinet '${REMOTE_FILE}' && \
     sudo chmod 640 '${REMOTE_FILE}'" < "${SCRIPT_DIR}/settings_overlay.py"
