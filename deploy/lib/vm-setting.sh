#!/usr/bin/env bash
# Read one effective clarinet setting from a deployed VM, honouring the same
# layering clarinet itself uses: settings.custom.toml (stand overlay) wins
# over settings.toml. Single source of truth for smoke tests and Makefile
# targets — keep the layering logic here only.
#
# Usage: vm-setting.sh <vm_ip> <key>
# Self-contained: sources vm.conf for SSH_KEY_PATH/KNOWN_HOSTS_FILE unless the
# caller already exported them (plain `. vm.conf` does not export).
set -euo pipefail

IP="${1:?usage: vm-setting.sh <vm_ip> <key>}"
KEY="${2:?usage: vm-setting.sh <vm_ip> <key>}"

if [[ -z "${SSH_KEY_PATH:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../vm/vm.conf
    source "${SCRIPT_DIR}/../vm/vm.conf"
fi

# The key is passed to python via argv (not interpolated into the source), but
# it still travels through an ssh command line — keep it to identifier chars.
if [[ ! "$KEY" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "vm-setting.sh: invalid key '$KEY'" >&2
    exit 2
fi

exec ssh -o StrictHostKeyChecking=no \
    -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}" \
    -i "${SSH_KEY_PATH:?vm.conf not sourced (SSH_KEY_PATH unset)}" \
    "clarinet@${IP}" \
    "python3 -c \"
import sys, tomllib, pathlib
m = {}
for p in ('/opt/clarinet/settings.toml', '/opt/clarinet/settings.custom.toml'):
    f = pathlib.Path(p)
    if f.is_file():
        m.update(tomllib.load(f.open('rb')))
print(m.get(sys.argv[1], ''))\" ${KEY}" 2>/dev/null
