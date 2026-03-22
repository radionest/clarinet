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

echo "-------------------------------------------"
echo "Results: ${passed}/${total} passed, ${failed} failed"

if [[ $failed -gt 0 ]]; then
    exit 1
fi
