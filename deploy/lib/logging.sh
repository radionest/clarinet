#!/usr/bin/env bash
# Portable logging — works on host and target VM.
# Usage: source logging.sh; init_logging "tag"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

init_logging() {
    _LOG_TAG="${1:?init_logging requires a tag}"
    # shellcheck disable=SC2329  # functions invoked by callers who source this file
    log()  { echo -e "${GREEN}[${_LOG_TAG}]${NC} $*" >&2; }
    # shellcheck disable=SC2329
    warn() { echo -e "${YELLOW}[${_LOG_TAG}]${NC} $*" >&2; }
    # shellcheck disable=SC2329
    err()  { echo -e "${RED}[${_LOG_TAG}]${NC} $*" >&2; }
}
