#!/usr/bin/env bash
# Smoke tests for Clarinet deployment
# Usage: smoke-test.sh <vm_ip> [path_prefix]
# Runs from the host against a deployed VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_CONF="${SCRIPT_DIR}/../vm/vm.conf"

# Load path prefix from vm.conf if not passed as argument
if [[ -f "$VM_CONF" ]]; then
    source "$VM_CONF"
fi

IP="${1:-}"
PREFIX="${2:-${PATH_PREFIX:-/}}"

if [[ -z "$IP" ]]; then
    # Try to get IP from vm.sh
    IP="$(bash "${SCRIPT_DIR}/../vm/vm.sh" ip 2>/dev/null)" || true
    if [[ -z "$IP" ]]; then
        echo "Usage: smoke-test.sh <vm_ip> [path_prefix]"
        exit 1
    fi
fi

BASE_URL="https://${IP}${PREFIX}"
CURL="curl -skf --max-time 10"
CURL_QUIET="curl -sk --max-time 10 -o /dev/null -w %{http_code}"

passed=0
failed=0
total=0

check() {
    local name="$1"
    local result="$2"
    local expected="$3"
    total=$((total + 1))

    if [[ "$result" == "$expected" ]]; then
        echo "  PASS  $name"
        passed=$((passed + 1))
    else
        echo "  FAIL  $name (expected: $expected, got: $result)"
        failed=$((failed + 1))
    fi
}

vm_setting() {
    # vm_setting <key> — read a setting from the VM with the same layering
    # clarinet uses: settings.custom.toml (stand overlay, written for
    # downstream-project deployments) overrides settings.toml.
    local key="$1"
    ssh -o StrictHostKeyChecking=no \
        -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}" \
        -i "$SSH_KEY_PATH" "clarinet@${IP}" \
        "python3 -c \"
import tomllib, pathlib
m = {}
for p in ('/opt/clarinet/settings.toml', '/opt/clarinet/settings.custom.toml'):
    f = pathlib.Path(p)
    if f.is_file():
        m.update(tomllib.load(f.open('rb')))
print(m.get('${key}', ''))\"" 2>/dev/null
}

echo "Smoke testing: ${BASE_URL}"
echo "-------------------------------------------"

# Test 1: HTTPS responds
echo "[1] Nginx HTTPS"
status=$($CURL_QUIET "${BASE_URL}" || echo "000")
check "HTTPS responds (200)" "$status" "200"

# Test 2: HTTP redirects to HTTPS
echo "[2] HTTP redirect"
status=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' "http://${IP}${PREFIX}" || echo "000")
check "HTTP->HTTPS redirect (301)" "$status" "301"

# Test 3: Health endpoint
echo "[3] Health endpoint"
status=$($CURL_QUIET "${BASE_URL}api/health" || echo "000")
check "GET /api/health (200)" "$status" "200"

health_body=$($CURL "${BASE_URL}api/health" 2>/dev/null || echo "{}")
health_status=$(echo "$health_body" | jq -r '.status // "unknown"')
check "Health status is ok" "$health_status" "ok"

db_status=$(echo "$health_body" | jq -r '.database // "unknown"')
check "Database status is ok" "$db_status" "ok"

pipeline_status=$(echo "$health_body" | jq -r '.pipeline // "unknown"')
check "Pipeline status is ok" "$pipeline_status" "ok"

# Test 4: Auth endpoint exists
echo "[4] Auth endpoint"
status=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}api/auth/login" || echo "000")
check "POST /api/auth/login (422 = exists)" "$status" "422"

# Test 5: Frontend SPA
echo "[5] Frontend SPA"
content_type=$($CURL -s -o /dev/null -D - "${BASE_URL}" 2>/dev/null | grep -i "content-type" | head -1 || echo "")
has_html=$(echo "$content_type" | grep -ci "text/html" || echo "0")
check "SPA serves HTML" "$has_html" "1"

# Test 6: Auth cookie flow (login → cookie → /auth/me)
echo "[6] Auth cookie flow"
COOKIE_FILE=$(mktemp)
trap 'rm -f "$COOKIE_FILE"' EXIT

# Read admin password from VM settings (custom.toml overlay wins)
ADMIN_PASS=$(vm_setting admin_password || echo "")

if [[ -n "$ADMIN_PASS" ]]; then
    login_status=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
        -c "$COOKIE_FILE" \
        -X POST "${BASE_URL}api/auth/login" \
        -d "username=admin@clarinet.ru&password=${ADMIN_PASS}" || echo "000")
    check "Login returns 204" "$login_status" "204"

    me_status=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
        -b "$COOKIE_FILE" \
        "${BASE_URL}api/auth/me" || echo "000")
    check "GET /auth/me with cookie (200)" "$me_status" "200"
else
    echo "  SKIP  Cannot read admin_password from VM"
fi

# Test 7: Quarto CLI (auto-provisioned on test VMs — see vm.sh cmd_deploy)
echo "[7] Quarto CLI"
SSH_VM=(ssh -o StrictHostKeyChecking=no \
    -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE:-$HOME/.ssh/known_hosts}" \
    -i "$SSH_KEY_PATH" "clarinet@${IP}")
# Resolve the Quarto location from the VM's settings (storage_path) instead of
# hardcoding the default layout — a relocated storage must not turn into SKIP.
VM_STORAGE=$(vm_setting storage_path) || VM_STORAGE=""
QUARTO_BIN="${VM_STORAGE:-/var/lib/clarinet/data}/quarto/bin/quarto"
if "${SSH_VM[@]}" "test -x '${QUARTO_BIN}'" 2>/dev/null; then
    # /opt/clarinet carries a downstream-style .env.example (e2e fixture), so a
    # clean `quarto check` from there is the regression test for the
    # neutral-cwd fix in quarto_status(). The CLI exits 0 even when the check
    # reports problems — assert on the output, not just the exit code.
    quarto_rc=0
    quarto_out=$(timeout 150 "${SSH_VM[@]}" \
        "cd /opt/clarinet && ./venv/bin/clarinet quarto status" 2>&1) || quarto_rc=$?
    check "clarinet quarto status exits 0" "$quarto_rc" "0"
    has_exe=$(echo "$quarto_out" | grep -c "Quarto executable:" || true)
    check "quarto status reports executable" "$has_exe" "1"
    has_problems=$(echo "$quarto_out" | grep -c "reported problems" || true)
    check "quarto check is clean" "$has_problems" "0"
    if [[ "$quarto_rc" != "0" || "$has_problems" != "0" ]]; then
        echo "$quarto_out" | tail -15
    fi
else
    # CLARINET_E2E_REQUIRE_QUARTO=1 (set by test-all-stages) turns a missing
    # Quarto install into a failure — a silent SKIP would hide a provisioning
    # regression behind a green pipeline.
    if [[ "${CLARINET_E2E_REQUIRE_QUARTO:-0}" == "1" ]]; then
        check "Quarto provisioned (required)" "missing" "present"
    else
        echo "  SKIP  Quarto not installed on this deployment"
    fi
fi

echo "-------------------------------------------"
echo "Results: ${passed}/${total} passed, ${failed} failed"

if [[ $failed -gt 0 ]]; then
    exit 1
fi
