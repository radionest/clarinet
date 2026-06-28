#!/usr/bin/env bash
# Idempotently provision a deployed stand for the nir_liver workflow test.
#
# The bare clarinet deploy ships an API + one default pipeline worker. The full
# workflow additionally needs the DICOM pipeline to run (anonymize-study,
# create-nifty) and Orthanc to answer Query/Retrieve. This script wires the gap:
#
#   1. dicom_retrieve_mode = "c-get" in settings.custom.toml — C-GET pulls pixel
#      data on the same association, so no second Storage SCP / no C-MOVE
#      modality round-trip is needed on the stand.
#   2. enable + start clarinet-worker@dicom — consumes the project's
#      <namespace>.dicom queue (anonymize / convert-to-nifti tasks).
#   3. register the clarinet AET as an Orthanc DICOM modality so Q/R is allowed
#      for it (Orthanc denies C-FIND/C-GET to unknown AETs by default). This is
#      also done by deploy/install/setup-services.sh; repeated here so the test
#      is self-sufficient against any stand and the call is idempotent.
#
# Usage:  setup_stand.sh <ssh_target> <ssh_key> [known_hosts]
# Safe to re-run.
set -euo pipefail

SSH_TARGET="${1:?usage: setup_stand.sh <ssh_target> <ssh_key> [known_hosts]}"
SSH_KEY="${2:?usage: setup_stand.sh <ssh_target> <ssh_key> [known_hosts]}"
KNOWN_HOSTS="${3:-}"

ssh_opts=(-o StrictHostKeyChecking=no -i "$SSH_KEY")
[[ -n "$KNOWN_HOSTS" ]] && ssh_opts+=(-o "UserKnownHostsFile=${KNOWN_HOSTS}")
run() { ssh "${ssh_opts[@]}" "$SSH_TARGET" "$@"; }

# 1. C-GET retrieve mode (stand overlay; higher priority than project settings.toml)
run "grep -q '^dicom_retrieve_mode' /opt/clarinet/settings.custom.toml 2>/dev/null || \
     printf '\n# workflow test: C-GET avoids a second Storage SCP\ndicom_retrieve_mode = \"c-get\"\n' \
     | sudo tee -a /opt/clarinet/settings.custom.toml >/dev/null"

# 2. DICOM pipeline worker
run "sudo systemctl enable --now clarinet-worker@dicom"

# 3. Orthanc modality for the clarinet AET (Q/R permission)
run "curl -sf -u orthanc:orthanc -X PUT http://localhost:8042/modalities/clarinet \
     -H 'Content-Type: application/json' \
     -d '{\"AET\":\"CLARINET\",\"Host\":\"127.0.0.1\",\"Port\":11112,\"AllowFind\":true,\"AllowGet\":true,\"AllowMove\":true,\"AllowStore\":true}' \
     >/dev/null && echo 'modality registered' || echo 'modality registration skipped'"

# Restart the dicom worker so it picks up the c-get override, and the API so it
# reloads settings (best-effort).
run "sudo systemctl restart clarinet-worker@dicom clarinet-api"
echo "stand provisioned for workflow test"
