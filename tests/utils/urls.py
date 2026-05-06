"""Centralized URL constants for API integration tests.

Constants match the router prefixes registered in ``src/api/app.py``.
Dynamic paths stay as f-strings: ``f"{RECORDS_BASE}/{record_id}/status"``.
"""

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

# --- Slicer ---
SLICER_BASE = "/api/slicer"
SLICER_PING = "/api/slicer/ping"

# --- DICOM ---
DICOM_BASE = "/api/dicom"

# --- Health ---
HEALTH = "/api/health"

# --- Pipelines ---
PIPELINES_BASE = "/api/pipelines"
PIPELINES_SYNC = "/api/pipelines/sync"

# --- Viewers ---
# Dynamic: f"{RECORDS_BASE}/{record_id}/viewers"
# Dynamic: f"{RECORDS_BASE}/{record_id}/viewers/{viewer_name}"

# --- DICOMweb (outside /api prefix for OHIF compatibility) ---
DICOMWEB_BASE = "/dicom-web"
