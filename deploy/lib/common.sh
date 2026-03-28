#!/usr/bin/env bash
# Host-only deploy utilities.
# Must be sourced after vm.conf (needs VM_USER, SSH_KEY_PATH) and after init_logging.

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$COMMON_DIR/logging.sh"

# require_commands cmd1 cmd2 ... — exit with hint if any missing
require_commands() {
    local missing=()
    for cmd in "$@"; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        err "Missing dependencies: ${missing[*]}"
        exit 1
    fi
}

# ssh_vm [args...] — SSH to VM using VM_USER, SSH_KEY_PATH, _get_ip()
ssh_vm() {
    local ip
    ip="$(_get_ip)"
    if [[ -z "$ip" ]]; then
        err "Could not determine VM IP. Is the VM running?"
        exit 1
    fi
    ssh -o StrictHostKeyChecking=no -i "$SSH_KEY_PATH" "${VM_USER}@${ip}" "$@"
}
