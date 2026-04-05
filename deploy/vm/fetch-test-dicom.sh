#!/usr/bin/env bash
# Fetch DICOM studies from a source Orthanc, anonymize, and save locally.
# Used by vm.sh cmd_bake() to prepare test data for the golden image.
#
# Usage: fetch-test-dicom.sh [--url URL] [--patient NAME] [--limit N] [--output DIR]
#   Defaults come from vm.conf (DICOM_SOURCE_URL, DICOM_SOURCE_PATIENT).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$DEPLOY_DIR/lib/logging.sh"
init_logging "fetch-dicom"

# Defaults from vm.conf (may be overridden by CLI args)
source "$SCRIPT_DIR/vm.conf"
ORTHANC_URL="${DICOM_SOURCE_URL:-}"
PATIENT_NAME="${DICOM_SOURCE_PATIENT:-}"
OUTPUT_DIR=""
LIMIT="${DICOM_SOURCE_LIMIT:-0}"  # 0 = no limit

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)      ORTHANC_URL="$2"; shift 2 ;;
        --patient)  PATIENT_NAME="$2"; shift 2 ;;
        --output)   OUTPUT_DIR="$2"; shift 2 ;;
        --limit)    LIMIT="$2"; shift 2 ;;
        *)          err "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "$ORTHANC_URL" ]]; then
    err "No source Orthanc URL. Set DICOM_SOURCE_URL in vm.conf or pass --url"
    exit 1
fi

if [[ -z "$PATIENT_NAME" ]]; then
    read -rp "Patient name to fetch (e.g. 'mishin'): " PATIENT_NAME
    if [[ -z "$PATIENT_NAME" ]]; then
        err "Patient name is required"
        exit 1
    fi
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$(mktemp -d -t fetch-dicom-XXXXXX)"
fi
mkdir -p "$OUTPUT_DIR"

# Check source Orthanc is reachable
if ! curl -sf --connect-timeout 5 "${ORTHANC_URL}/system" > /dev/null 2>&1; then
    err "Cannot reach source Orthanc at ${ORTHANC_URL}"
    err "Make sure the server is running and accessible"
    exit 1
fi

log "Source Orthanc: ${ORTHANC_URL}"
log "Patient: ${PATIENT_NAME}"

# Find all studies for the patient (wildcard for case-insensitive partial match)
study_ids=$(curl -sf -X POST "${ORTHANC_URL}/tools/find" \
    -H "Content-Type: application/json" \
    -d "{\"Level\":\"Study\",\"Query\":{\"PatientName\":\"*${PATIENT_NAME}*\"},\"Expand\":false}" \
) || { err "Failed to query studies"; exit 1; }

study_count=$(echo "$study_ids" | jq -r 'length')
if [[ "$study_count" -eq 0 ]]; then
    err "No studies found for patient '${PATIENT_NAME}'"
    exit 1
fi

# Apply limit
if [[ "$LIMIT" -gt 0 && "$study_count" -gt "$LIMIT" ]]; then
    log "Found ${study_count} study(ies), limiting to ${LIMIT}"
    study_ids=$(echo "$study_ids" | jq ".[0:${LIMIT}]")
    study_count="$LIMIT"
else
    log "Found ${study_count} study(ies)"
fi

# Build anonymized patient name from study descriptions (truncated to stay reasonable)
# e.g. TEST^CT_HEAD+MR_BRAIN
build_anon_patient_name() {
    local descriptions=()
    for study_id in $(echo "$study_ids" | jq -r '.[]'); do
        local desc
        desc=$(curl -sf "${ORTHANC_URL}/studies/${study_id}" \
            | jq -r '.MainDicomTags.StudyDescription // empty')

        if [[ -n "$desc" ]]; then
            # Normalize: uppercase, spaces to underscores, keep only alphanum and underscore
            local norm
            norm=$(echo "$desc" | tr '[:lower:]' '[:upper:]' | tr ' ' '_' | sed 's/[^A-Z0-9_]//g')
            # Truncate individual descriptions to 20 chars
            descriptions+=("${norm:0:20}")
        fi
    done

    if [[ ${#descriptions[@]} -gt 0 ]]; then
        local joined
        joined=$(IFS='+'; echo "${descriptions[*]}")
        # Truncate total to 64 chars (DICOM PatientName max is 64)
        echo "TEST^${joined:0:59}"
    else
        echo "TEST^ANON_STUDY"
    fi
}

anon_patient_name=$(build_anon_patient_name)
log "Anonymized PatientName: ${anon_patient_name}"

total_size=0

for study_id in $(echo "$study_ids" | jq -r '.[]'); do
    # Get study description for logging
    study_desc=$(curl -sf "${ORTHANC_URL}/studies/${study_id}" \
        | jq -r '.MainDicomTags.StudyDescription // "unknown"')
    log "Processing study: ${study_desc} (${study_id})"

    # Anonymize the study via Orthanc API
    anon_response=$(curl -sf -X POST "${ORTHANC_URL}/studies/${study_id}/anonymize" \
        -H "Content-Type: application/json" \
        -d "{
            \"Replace\": {
                \"PatientName\": \"${anon_patient_name}\",
                \"PatientID\": \"TEST-001\"
            },
            \"Keep\": [
                \"StudyDescription\",
                \"SeriesDescription\",
                \"Modality\",
                \"BodyPartExamined\",
                \"StudyDate\",
                \"SeriesDate\"
            ],
            \"KeepPrivateTags\": false,
            \"Force\": true
        }") || { err "Failed to anonymize study ${study_id}"; exit 1; }

    anon_study_id=$(echo "$anon_response" | jq -r '.ID')
    if [[ -z "$anon_study_id" || "$anon_study_id" == "null" ]]; then
        err "Anonymization returned no study ID for ${study_id}"
        err "Response: ${anon_response}"
        exit 1
    fi

    log "  Anonymized → ${anon_study_id}"

    # Filter series: keep only those with >100 instances, max 3 per study.
    # Delete unwanted series from the anonymized copy before downloading.
    anon_series=$(curl -sf "${ORTHANC_URL}/studies/${anon_study_id}" | jq -r '.Series[]')
    kept=0
    for series_id in $anon_series; do
        inst_count=$(curl -sf "${ORTHANC_URL}/series/${series_id}" | jq '.Instances | length')
        series_desc=$(curl -sf "${ORTHANC_URL}/series/${series_id}" \
            | jq -r '.MainDicomTags.SeriesDescription // "unnamed"')
        if [[ "$inst_count" -le 100 ]] || [[ "$kept" -ge 3 ]]; then
            curl -sf -X DELETE "${ORTHANC_URL}/series/${series_id}" > /dev/null 2>&1 || true
            log "    Skipped series: ${series_desc} (${inst_count} instances)"
        else
            kept=$((kept + 1))
            log "    Kept series: ${series_desc} (${inst_count} instances)"
        fi
    done

    if [[ "$kept" -eq 0 ]]; then
        warn "  No series with >100 instances — skipping study"
        curl -sf -X DELETE "${ORTHANC_URL}/studies/${anon_study_id}" > /dev/null 2>&1 || true
        continue
    fi

    # Download as ZIP archive
    local_zip="${OUTPUT_DIR}/${anon_study_id}.zip"
    log "  Downloading archive..."
    curl -sf "${ORTHANC_URL}/studies/${anon_study_id}/archive" \
        --progress-bar -o "$local_zip"

    zip_size=$(stat -c%s "$local_zip" 2>/dev/null || stat -f%z "$local_zip")
    total_size=$((total_size + zip_size))
    log "  Archive: $(numfmt --to=iec "$zip_size")"

    # Extract DICOM files
    unzip -q -o "$local_zip" -d "$OUTPUT_DIR"
    rm -f "$local_zip"

    # Clean up the anonymized study from source Orthanc (it's a temporary copy)
    curl -sf -X DELETE "${ORTHANC_URL}/studies/${anon_study_id}" > /dev/null 2>&1 || true
done

file_count=$(find "$OUTPUT_DIR" -type f | wc -l)
log "Done: ${file_count} files, $(numfmt --to=iec "$total_size") total"
log "Output: ${OUTPUT_DIR}"

# Print output dir path for caller to capture
echo "$OUTPUT_DIR"
