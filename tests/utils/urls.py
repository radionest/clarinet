"""Centralized URL constants for API integration tests.

Constants match the router prefixes registered in ``src/api/app.py``.
Dynamic paths stay as f-strings: ``f"{RECORDS_BASE}/{record_id}/status"``.
"""

# --- Headers ---
# Per-client Slicer storage path override. Set by the frontend from
# localStorage; honored by /slicer/records/{id}/open, /slicer/records/{id}/validate,
# and /records/{id}/submit (POST + PATCH). See clarinet/api/dependencies.py.
X_CLARINET_STORAGE_PATH_HEADER = "X-Clarinet-Storage-Path-Client"

# --- Auth ---
AUTH_BASE = "/api/auth"
AUTH_LOGIN = "/api/auth/login"
AUTH_LOGOUT = "/api/auth/logout"
AUTH_REGISTER = "/api/auth/register"
AUTH_ME = "/api/auth/me"
AUTH_SESSION_VALIDATE = "/api/auth/session/validate"
AUTH_SESSION_REFRESH = "/api/auth/session/refresh"
AUTH_SESSIONS_ACTIVE = "/api/auth/sessions/active"

# --- Users ---
USERS_BASE = "/api/user"
USERS_ME = "/api/user/me"
USERS_ME_ROLES = "/api/user/me/roles"
USERS_ROLES = "/api/user/roles"

# --- Records ---
RECORDS_BASE = "/api/records"
RECORDS_FIND = "/api/records/find"
RECORDS_FIND_RANDOM = "/api/records/find/random"
RECORDS_AVAILABLE_TYPES = "/api/records/available_types"
RECORDS_FILTER_OPTIONS = "/api/records/filter-options"
RECORDS_BULK_STATUS = "/api/records/bulk/status"

# Dynamic: f"{RECORDS_BASE}/{record_id}/data/prefill"
# Dynamic: f"{RECORDS_BASE}/{record_id}/context-info"
# Dynamic: f"{RECORDS_BASE}/{record_id}/output-files/{file_name}"

# --- Record types ---
RECORD_TYPES = "/api/records/types"
RECORD_TYPES_FIND = "/api/records/types/find"

# --- Patients ---
PATIENTS_BASE = "/api/patients"

# --- Studies ---
STUDIES_BASE = "/api/studies"

# --- Series ---
SERIES_BASE = "/api/series"
SERIES_RANDOM = "/api/series/random"
SERIES_FIND = "/api/series/find"

# --- Admin ---
ADMIN_BASE = "/api/admin"
ADMIN_STATS = "/api/admin/stats"
ADMIN_RT_STATS = "/api/admin/record-types/stats"
ADMIN_RECORDS = "/api/admin/records"  # + /{id} for cascade delete
ADMIN_RECORD_STATUS = "/api/admin/records"  # + /{id}/status
ADMIN_RECORD_USER = "/api/admin/records"  # + /{id}/user
ADMIN_RECORD_OUTPUT_FILES = "/api/admin/records"  # + /{id}/output-files

# --- Reports ---
ADMIN_REPORTS = "/api/admin/reports"  # GET list; + /{name}/download for file

# --- Quarto Reports ---
# GET list; + /{name}/render (POST), /{name}/renders/{render_id}/status|download
ADMIN_QUARTO_REPORTS = "/api/admin/quarto-reports"


def admin_quarto_render(name: str) -> str:
    return f"{ADMIN_QUARTO_REPORTS}/{name}/render"


def admin_quarto_render_status(name: str, render_id: str) -> str:
    return f"{ADMIN_QUARTO_REPORTS}/{name}/renders/{render_id}/status"


def admin_quarto_render_download(name: str, render_id: str) -> str:
    return f"{ADMIN_QUARTO_REPORTS}/{name}/renders/{render_id}/download"


# --- Workflow visualization (admin) ---
WORKFLOW_BASE = "/api/admin/workflow"
WORKFLOW_GRAPH = "/api/admin/workflow/graph"
WORKFLOW_DRY_RUN = "/api/admin/workflow/dry-run"
WORKFLOW_FIRE = "/api/admin/workflow/fire"
WORKFLOW_DISPATCH_DRY_RUN = "/api/admin/workflow/dispatch-dry-run"
WORKFLOW_DISPATCH = "/api/admin/workflow/dispatch"

# --- Slicer ---
SLICER_BASE = "/api/slicer"
SLICER_PING = "/api/slicer/ping"

# --- DICOM ---
DICOM_BASE = "/api/dicom"
DICOM_IMPORT_STUDY = "/api/dicom/import-study"
# Dynamic: f"{DICOM_BASE}/patient/{patient_id}/studies"
# Dynamic: f"{DICOM_BASE}/studies/{study_uid}/anonymize"

# --- Health ---
HEALTH = "/api/health"

# --- Pipelines ---
PIPELINES_BASE = "/api/pipelines"
PIPELINES_SYNC = "/api/pipelines/sync"
PIPELINE_RUNS = "/api/pipelines/runs"


def pipeline_run_url(task_id: str) -> str:
    return f"{PIPELINE_RUNS}/{task_id}"


def record_runs_url(record_id: int) -> str:
    return f"{RECORDS_BASE}/{record_id}/runs"


# --- Viewers ---
# Dynamic: f"{RECORDS_BASE}/{record_id}/viewers"
# Dynamic: f"{RECORDS_BASE}/{record_id}/viewers/{viewer_name}"

# --- DICOMweb (outside /api prefix for OHIF compatibility) ---
DICOMWEB_BASE = "/dicom-web"
