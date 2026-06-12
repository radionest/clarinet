#!/usr/bin/env bash
# Launch (or stop) a headless 3D Slicer for the Clarinet slicer/dicom tests.
#
# Runs a FULL-GUI Slicer under Xvfb (layoutManager() is None with
# --no-main-window, which breaks the slice-widget tests) and starts its Web
# Server + /slicer/exec endpoint via webserver.py. The slicer/dicom tests then
# connect to it on CLARINET_TEST_SLICER_HOST:PORT.
#
# Usage:
#   run-headless.sh          launch + verify; exits non-zero (with install
#                            hints) if Slicer can't be found or started — this
#                            is what lets `make test-all-stages` fail early.
#   run-headless.sh --stop   stop a running instance.
#
# Slicer binary discovery: $SLICER_HOME/Slicer, else the newest match of
# ~/Slicer-*/Slicer, /opt/Slicer-*/Slicer, ~/Slicer/Slicer, /opt/Slicer/Slicer.
# Install + env details: deploy/test/slicer/README.md.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBSERVER_PY="$SCRIPT_DIR/webserver.py"
PORT="${CLARINET_TEST_SLICER_PORT:-2016}"
HOST="${CLARINET_TEST_SLICER_HOST:-localhost}"
LOG="${SLICER_HEADLESS_LOG:-/tmp/clarinet-slicer-headless.log}"

die() { echo "[slicer-headless] ERROR: $*" >&2; exit 1; }

# pkill patterns below are safe: this script's own command line is the script
# file path ("bash .../run-headless.sh"), which does not contain "webserver.py"
# or "storescp", so pkill never matches itself.
stop() {
    pkill -f 'webserver.py' 2>/dev/null || true
    pkill -x storescp 2>/dev/null || true
}

if [ "${1:-}" = "--stop" ]; then
    stop
    echo "[slicer-headless] stopped"
    exit 0
fi

# --- locate the Slicer binary -------------------------------------------------
SLICER_BIN=""
if [ -n "${SLICER_HOME:-}" ] && [ -x "$SLICER_HOME/Slicer" ]; then
    SLICER_BIN="$SLICER_HOME/Slicer"
else
    for d in "$HOME"/Slicer-*/ /opt/Slicer-*/ "$HOME"/Slicer/ /opt/Slicer/; do
        [ -x "${d}Slicer" ] && SLICER_BIN="${d}Slicer"
    done
fi
[ -n "$SLICER_BIN" ] || die "3D Slicer not found. Install it (see \
deploy/test/slicer/README.md) and set SLICER_HOME, \
e.g. SLICER_HOME=~/Slicer-5.10.0-linux-amd64"

command -v xvfb-run >/dev/null || die "xvfb-run not found — install xvfb \
(e.g. sudo apt-get install -y xvfb)"

# --- already serving? ---------------------------------------------------------
if curl -s --max-time 4 -X POST "http://$HOST:$PORT/slicer/exec" \
        --data-binary '__execResult={"up":1}' 2>/dev/null | grep -q '"up": 1'; then
    echo "[slicer-headless] already serving on $HOST:$PORT"
    exit 0
fi

# --- clean stale procs that would block the port / storage listener -----------
stop
sleep 1

# --- launch detached so it outlives this script -------------------------------
echo "[slicer-headless] launching $SLICER_BIN (Web Server :$PORT, cold boot ~30-60s)..."
setsid xvfb-run -a "$SLICER_BIN" --no-splash --python-script "$WEBSERVER_PY" \
    > "$LOG" 2>&1 < /dev/null &
LAUNCH_PID=$!

# --- wait for the server to announce itself (or for the launch group to die) --
for _ in $(seq 1 120); do
    grep -q WEBSERVER_STARTED "$LOG" 2>/dev/null && break
    kill -0 "$LAUNCH_PID" 2>/dev/null \
        || die "Slicer exited during startup (see $LOG): $(tail -3 "$LOG" 2>/dev/null | tr '\n' ' ')"
    sleep 2
done
grep -q WEBSERVER_STARTED "$LOG" 2>/dev/null \
    || die "timed out waiting for Slicer Web Server (see $LOG)"
echo "[slicer-headless] $(grep WEBSERVER_STARTED "$LOG" | tail -1)"

# --- verify the exec contract end-to-end --------------------------------------
RESP="$(curl -s --max-time 10 -X POST "http://$HOST:$PORT/slicer/exec" \
    --data-binary 'import slicer
__execResult = {"ok": 1+1, "os": slicer.app.os}' 2>&1)"
if printf '%s' "$RESP" | grep -q '"ok": 2'; then
    echo "[slicer-headless] OK — serving on $HOST:$PORT"
else
    die "Web Server up but /slicer/exec failed (is enableExec on?): ${RESP:-<empty>}"
fi
