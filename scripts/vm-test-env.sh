#!/usr/bin/env bash
# Print the `export` lines that point the test suite at the pipeline VM's own
# services: its Orthanc as PACS and its RabbitMQ as broker. `make
# test-all-stages` stage 5 and scripts/vm-run-tests.sh (the PostgreSQL pass)
# both `eval` this output, so the two cannot drift apart again (#624).
#
# Forced, not defaulted: an operator's CLARINET_TEST_PACS_* / _RABBITMQ_* (from
# the environment or .env.test, which tests/config.py loads with setdefault)
# are meant for their own services. Honouring them here would seed and C-STORE
# into their PACS, make the C-MOVE probe ask the wrong host and skip, or send
# the pipeline tests to a broker that rejects them with ACCESS_REFUSED.
#
# Usage: eval "$(vm-test-env.sh <vm_ip>)"   (values are POSIX-quoted: sh or bash)
set -euo pipefail

IP="${1:?usage: vm-test-env.sh <vm_ip>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VMSET="$SCRIPT_DIR/../deploy/lib/vm-setting.sh"

# The VM's broker user is an administrator (deploy/install/setup-services.sh),
# so it also serves the management API the pipeline tests purge queues through.
rmq_user=$(bash "$VMSET" "$IP" rabbitmq_login || true)
rmq_pass=$(bash "$VMSET" "$IP" rabbitmq_password || true)
if [[ -z "$rmq_user" || -z "$rmq_pass" ]]; then
    echo "vm-test-env.sh: cannot read rabbitmq_login/rabbitmq_password from the VM at $IP" >&2
    exit 1
fi

emit() {
    local sq="'"
    printf "export %s='%s'\n" "$1" "${2//$sq/$sq\\$sq$sq}"
}

emit CLARINET_TEST_PACS_HOST "$IP"
# "" = drop the C-MOVE reachability probe: the NAT VM can reach this host.
emit CLARINET_TEST_PACS_SSH ""
emit CLARINET_TEST_RABBITMQ_HOST "$IP"
emit CLARINET_TEST_RABBITMQ_PORT 5672
emit CLARINET_TEST_RABBITMQ_MANAGEMENT_PORT 15672
emit CLARINET_TEST_RABBITMQ_USER "$rmq_user"
emit CLARINET_TEST_RABBITMQ_PASS "$rmq_pass"
emit CLARINET_TEST_RABBITMQ_MANAGEMENT_USER "$rmq_user"
emit CLARINET_TEST_RABBITMQ_MANAGEMENT_PASS "$rmq_pass"
# The broker is known to exist here, so an unreachable one fails the pipeline
# tests instead of letting the stage finish green without running them.
emit CLARINET_TEST_REQUIRE_RABBITMQ 1
