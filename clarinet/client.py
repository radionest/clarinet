"""
Clarinet API Client.

This module provides a Python client for interacting with the Clarinet API,
supporting both low-level API calls and high-level convenience methods.
"""

import getpass
from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Literal
from uuid import UUID

import httpx

from clarinet.models import (
    Patient,
    PatientRead,
    PatientSave,
    RecordCreate,
    RecordPage,
    RecordRead,
    RecordStatus,
    RecordType,
    RecordTypeCreate,
    RecordTypeFind,
    Series,
    SeriesCreate,
    SeriesFind,
    SeriesRead,
    Study,
    StudyCreate,
    StudyRead,
    UserRead,
)
from clarinet.types import RecordData
from clarinet.utils.logger import logger
from clarinet.utils.serialization import json_dumps_bytes

# Rebuild models to resolve forward references
PatientRead.model_rebuild()
StudyRead.model_rebuild()
SeriesRead.model_rebuild()
RecordRead.model_rebuild()


class ClarinetAPIError(Exception):
    """Base exception for Clarinet API errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: Any = None) -> None:
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        parts: list[str] = []
        if self.status_code is not None:
            parts.append(f"[{self.status_code}]")
        parts.append(self.message)
        if self.detail is not None:
            parts.append(f"(detail: {self.detail})")
        return " ".join(parts)


class ClarinetAuthError(ClarinetAPIError):
    """Authentication-related errors."""

    pass


class ClarinetClient:
    """Client for interacting with Clarinet API.

    This client handles authentication, session management, and provides
    both low-level API methods and high-level convenience functions.

    Example:
        ```python
        # With password prompt
        client = ClarinetClient("http://localhost:8000", username="admin@example.com")

        # With password provided
        client = ClarinetClient(
            "http://localhost:8000", username="admin@example.com", password="secret"
        )

        # Get current user
        user = await client.get_me()

        # Create multiple studies
        await client.create_studies_batch(
            [
                {"study_uid": "1.2.3", "date": "2024-01-01", "patient_id": "P001"},
                {"study_uid": "1.2.4", "date": "2024-01-02", "patient_id": "P002"},
            ]
        )
        ```
    """

    # Class-level defaults so AsyncMock(spec=ClarinetClient) sees these attributes.
    _authenticated: bool = False
    service_token: str | None = None

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        auto_login: bool = True,
        log_requests: bool = False,
        verify_ssl: bool = True,
        service_token: str | None = None,
    ) -> None:
        """Initialize Clarinet client.

        Args:
            base_url: Base URL of the Clarinet API (e.g., "http://localhost:8000")
            username: Username for authentication (optional if using existing session)
            password: Password for authentication. If None and username is provided,
                     will prompt for password interactively
            auto_login: Automatically login on initialization (default: True)
            log_requests: Enable request/response logging (default: False)
            verify_ssl: Verify SSL certificates (default: True). Set to False
                       for self-signed certificates.
            service_token: Static service token for internal clients. When set,
                          sent as X-Internal-Token header — no login() needed,
                          no AccessToken created in DB.
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.log_requests = log_requests
        self.service_token = service_token

        # Create async HTTP client with cookie jar for session management
        self.client = httpx.AsyncClient(
            base_url=self.base_url, follow_redirects=True, verify=verify_ssl
        )

        if service_token:
            self.client.headers["X-Internal-Token"] = service_token
            self._authenticated = True
        elif auto_login and username:
            # Note: __init__ cannot be async, so we'll login on first request
            # User can also call login() manually
            pass

    async def __aenter__(self) -> "ClarinetClient":
        """Async context manager entry."""
        if not self._authenticated and self.username:
            await self.login()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    def _log_request(self, method: str, url: str, **kwargs: Any) -> None:
        """Log HTTP request if logging is enabled."""
        if self.log_requests:
            logger.debug(f"API Request: {method} {url}", extra={"request_data": kwargs})

    def _log_response(self, response: httpx.Response) -> None:
        """Log HTTP response if logging is enabled."""
        if self.log_requests:
            logger.debug(
                f"API Response: {response.status_code}",
                extra={"response_data": response.text},
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        raise_for_status: bool = True,
        _retried: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make HTTP request to API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/auth/login")
            raise_for_status: Raise exception on HTTP errors
            _retried: Internal flag to prevent infinite retry loops.
            **kwargs: Additional arguments passed to httpx request

        Returns:
            HTTP response

        Raises:
            ClarinetAPIError: On API errors
            ClarinetAuthError: On authentication errors
        """
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        # Log BEFORE the orjson rewrite below — caller dicts are human-readable,
        # the post-rewrite `content` is raw bytes that loguru would serialize
        # opaquely.
        self._log_request(method, url, **kwargs)

        # Serialize JSON via orjson so UUID/datetime values from caller dicts
        # don't crash on stdlib json's encoder (httpx default). Normalize headers
        # through httpx.Headers so list-of-tuples / httpx.Headers callers work and
        # a caller-supplied Content-Type (any case) wins over our default.
        if "json" in kwargs:
            if "content" in kwargs:
                # httpx forbids passing both — fail loudly instead of silently
                # overwriting the caller's binary content with our JSON body.
                raise TypeError("ClarinetClient._request: pass either json= or content=, not both")
            kwargs["content"] = json_dumps_bytes(kwargs.pop("json"))
            headers = httpx.Headers(kwargs.get("headers") or {})
            headers.setdefault("Content-Type", "application/json")
            kwargs["headers"] = headers

        try:
            response = await self.client.request(method, url, **kwargs)
            self._log_response(response)

            if raise_for_status:
                if response.status_code == 401:
                    # Auto-retry with re-login (once, skip for auth endpoints)
                    if (
                        not _retried
                        and self.username
                        and self.password
                        and "/auth/" not in endpoint
                    ):
                        logger.info("Session expired, re-authenticating...")
                        await self.login()
                        return await self._request(
                            method,
                            endpoint,
                            raise_for_status=raise_for_status,
                            _retried=True,
                            **kwargs,
                        )
                    raise ClarinetAuthError(
                        "Authentication required or session expired",
                        status_code=401,
                        detail=response.text,
                    )
                elif response.status_code == 403:
                    raise ClarinetAuthError(
                        "Access forbidden",
                        status_code=403,
                        detail=response.text,
                    )
                elif response.status_code == 400:
                    detail = None
                    try:
                        detail = response.json()
                    except Exception:
                        detail = response.text

                    # For auth endpoints 400 means invalid credentials
                    if "/auth/" in endpoint:
                        raise ClarinetAuthError(
                            "Invalid credentials",
                            status_code=400,
                            detail=detail,
                        )
                    else:
                        raise ClarinetAPIError(
                            f"Bad request: {response.status_code}",
                            status_code=response.status_code,
                            detail=detail,
                        )
                elif response.status_code >= 400:
                    detail = None
                    try:
                        detail = response.json()
                    except Exception:
                        detail = response.text

                    raise ClarinetAPIError(
                        f"API error: {response.status_code}",
                        status_code=response.status_code,
                        detail=detail,
                    )

            return response

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during request: {e}")
            raise ClarinetAPIError(f"HTTP error: {e!s}") from e

    async def login(self, username: str | None = None, password: str | None = None) -> UserRead:
        """Authenticate with the API.

        Args:
            username: Username (uses instance username if not provided)
            password: Password (uses instance password if not provided,
                     prompts if neither is available)

        Returns:
            Current user information

        Raises:
            ClarinetAuthError: On authentication failure
        """
        if self.service_token:
            return await self.get_me()

        username = username or self.username
        password = password or self.password

        if not username:
            raise ClarinetAuthError("Username is required for login")

        if not password:
            password = getpass.getpass(f"Password for {username}: ")

        try:
            # Login endpoint expects form data
            response = await self._request(
                "POST",
                "/auth/login",
                data={"username": username, "password": password},
            )

            if response.status_code in [200, 204]:
                self._authenticated = True
                logger.info(f"Successfully authenticated as {username}")
                # Get current user info
                return await self.get_me()
            else:
                raise ClarinetAuthError(
                    "Login failed",
                    status_code=response.status_code,
                    detail=response.text,
                )

        except ClarinetAPIError:
            raise
        except Exception as e:
            logger.error(f"Login error: {e}")
            raise ClarinetAuthError(f"Login failed: {e!s}") from e

    async def logout(self) -> None:
        """Logout and clear session."""
        try:
            await self._request("POST", "/auth/logout", raise_for_status=False)
            self._authenticated = False
            logger.info("Successfully logged out")
        except Exception as e:
            logger.warning(f"Logout error (ignored): {e}")

    # ==================== User Management ====================

    async def get_me(self) -> UserRead:
        """Get current authenticated user information.

        Returns:
            Current user data

        Raises:
            ClarinetAuthError: If not authenticated
        """
        response = await self._request("GET", "/auth/me")
        return UserRead.model_validate(response.json())

    async def validate_session(self) -> UserRead:
        """Validate current session.

        Returns:
            Current user if session is valid

        Raises:
            ClarinetAuthError: If session is invalid
        """
        response = await self._request("GET", "/auth/session/validate")
        return UserRead.model_validate(response.json())

    # ==================== Patient Management ====================

    async def get_patients(self) -> list[PatientRead]:
        """Get all patients.

        Returns:
            List of all patients
        """
        response = await self._request("GET", "/patients")
        return [PatientRead.model_validate(p) for p in response.json()]

    async def get_patient(self, patient_id: str) -> PatientRead:
        """Get patient by ID.

        Args:
            patient_id: Patient ID

        Returns:
            Patient data
        """
        response = await self._request("GET", f"/patients/{patient_id}")
        return PatientRead.model_validate(response.json())

    async def create_patient(self, patient: PatientSave | dict[str, Any]) -> Patient:
        """Create a new patient.

        Args:
            patient: Patient data (model or dict)

        Returns:
            Created patient (without studies)
        """
        if isinstance(patient, dict):
            patient = PatientSave.model_validate(patient)

        response = await self._request(
            "POST",
            "/patients",
            json=patient.model_dump(by_alias=True, mode="json"),
        )
        return Patient.model_validate(response.json())

    async def anonymize_patient(self, patient_id: str) -> Patient:
        """Anonymize a patient.

        Args:
            patient_id: Patient ID to anonymize

        Returns:
            Updated patient with anonymous name (without studies)
        """
        response = await self._request("POST", f"/patients/{patient_id}/anonymize")
        return Patient.model_validate(response.json())

    # ==================== Study Management ====================

    async def get_studies(self) -> list[StudyRead]:
        """Get all studies.

        Returns:
            List of all studies
        """
        response = await self._request("GET", "/studies")
        return [StudyRead.model_validate(s) for s in response.json()]

    async def get_study(self, study_uid: str) -> StudyRead:
        """Get study by UID.

        Args:
            study_uid: Study UID

        Returns:
            Study data
        """
        response = await self._request("GET", f"/studies/{study_uid}")
        return StudyRead.model_validate(response.json())

    async def get_study_series(self, study_uid: str) -> list[Series]:
        """Get all series for a study.

        Args:
            study_uid: Study UID

        Returns:
            List of series in the study (without nested relations)
        """
        response = await self._request("GET", f"/studies/{study_uid}/series")
        return [Series.model_validate(s) for s in response.json()]

    async def create_study(self, study: StudyCreate | dict[str, Any]) -> Study:
        """Create a new study.

        Args:
            study: Study data (model or dict)

        Returns:
            Created study (without relations)
        """
        if isinstance(study, dict):
            # Handle date conversion if needed
            if "date" in study and isinstance(study["date"], str):
                study["date"] = date.fromisoformat(study["date"])
            study = StudyCreate.model_validate(study)

        response = await self._request(
            "POST",
            "/studies",
            json=study.model_dump(mode="json"),
        )
        return Study.model_validate(response.json())

    async def add_anonymized_study_uid(self, study_uid: str, anon_uid: str) -> Study:
        """Add anonymized UID to a study.

        Args:
            study_uid: Original study UID
            anon_uid: Anonymized study UID

        Returns:
            Updated study (without relations)
        """
        response = await self._request(
            "POST",
            f"/studies/{study_uid}/add_anonymized",
            params={"anon_uid": anon_uid},
        )
        return Study.model_validate(response.json())

    # ==================== Series Management ====================

    async def get_all_series(self) -> list[SeriesRead]:
        """Get all series.

        Returns:
            List of all series
        """
        response = await self._request("GET", "/series")
        return [SeriesRead.model_validate(s) for s in response.json()]

    async def get_series(self, series_uid: str) -> SeriesRead:
        """Get series by UID.

        Args:
            series_uid: Series UID

        Returns:
            Series data with related entities
        """
        response = await self._request("GET", f"/series/{series_uid}")
        return SeriesRead.model_validate(response.json())

    async def get_random_series(self) -> SeriesRead:
        """Get a random series.

        Returns:
            Random series
        """
        response = await self._request("GET", "/series/random")
        return SeriesRead.model_validate(response.json())

    async def create_series(self, series: SeriesCreate | dict[str, Any]) -> Series:
        """Create a new series.

        Args:
            series: Series data (model or dict)

        Returns:
            Created series (without relations)
        """
        if isinstance(series, dict):
            series = SeriesCreate.model_validate(series)

        response = await self._request(
            "POST",
            "/series",
            json=series.model_dump(mode="json"),
        )
        return Series.model_validate(response.json())

    async def find_series(self, find_query: SeriesFind | dict[str, Any]) -> list[SeriesRead]:
        """Find series by criteria.

        Args:
            find_query: Search criteria

        Returns:
            List of matching series
        """
        if isinstance(find_query, dict):
            find_query = SeriesFind.model_validate(find_query)

        response = await self._request(
            "POST",
            "/series/find",
            json=find_query.model_dump(exclude_none=True, mode="json"),
        )
        return [SeriesRead.model_validate(s) for s in response.json()]

    async def add_anonymized_series_uid(self, series_uid: str, anon_uid: str) -> Series:
        """Add anonymized UID to a series.

        Args:
            series_uid: Original series UID
            anon_uid: Anonymized series UID

        Returns:
            Updated series (without relations)
        """
        response = await self._request(
            "POST",
            f"/series/{series_uid}/add_anonymized",
            params={"anon_uid": anon_uid},
        )
        return Series.model_validate(response.json())

    # ==================== Record Type Management ====================

    async def get_record_types(self) -> list[RecordType]:
        """Get all record types.

        Returns:
            List of all record types
        """
        response = await self._request("GET", "/records/types")
        return [RecordType.model_validate(t) for t in response.json()]

    async def get_record_type(self, name: str) -> RecordType:
        """Get a record type by name.

        Args:
            name: Record type name (primary key).

        Returns:
            Record type data.
        """
        response = await self._request("GET", f"/records/types/{name}")
        return RecordType.model_validate(response.json())

    async def create_record_type(
        self,
        record_type: RecordTypeCreate | dict[str, Any],
        constrain_unique_names: bool = True,
    ) -> RecordType:
        """Create a new record type.

        Args:
            record_type: Record type data
            constrain_unique_names: Enforce unique record type names

        Returns:
            Created record type
        """
        if isinstance(record_type, dict):
            record_type = RecordTypeCreate.model_validate(record_type)

        response = await self._request(
            "POST",
            "/records/types",
            params={"constrain_unique_names": constrain_unique_names},
            json=record_type.model_dump(mode="json"),
        )
        return RecordType.model_validate(response.json())

    async def find_record_types(
        self, find_query: RecordTypeFind | dict[str, Any]
    ) -> list[RecordType]:
        """Find record types by criteria.

        Args:
            find_query: Search criteria

        Returns:
            List of matching record types
        """
        if isinstance(find_query, dict):
            find_query = RecordTypeFind.model_validate(find_query)

        response = await self._request(
            "POST",
            "/records/types/find",
            json=find_query.model_dump(exclude_none=True, mode="json"),
        )
        return [RecordType.model_validate(t) for t in response.json()]

    # ==================== Record Management ====================

    async def get_record(self, record_id: int) -> RecordRead:
        """Get record by ID.

        Args:
            record_id: Record ID

        Returns:
            Record data
        """
        response = await self._request("GET", f"/records/{record_id}")
        return RecordRead.model_validate(response.json())

    async def create_record(self, record: RecordCreate | dict[str, Any]) -> RecordRead:
        """Create a new record.

        Args:
            record: Record data

        Returns:
            Created record
        """
        if isinstance(record, dict):
            record = RecordCreate.model_validate(record)

        response = await self._request(
            "POST",
            "/records/",
            json=record.model_dump(mode="json"),
        )
        return RecordRead.model_validate(response.json())

    async def update_record_status(self, record_id: int, status: RecordStatus) -> RecordRead:
        """Update record status.

        Args:
            record_id: Record ID
            status: New record status

        Returns:
            Updated record
        """
        response = await self._request(
            "PATCH",
            f"/records/{record_id}/status",
            params={"record_status": status.value},
        )
        return RecordRead.model_validate(response.json())

    async def update_record_data(self, record_id: int, data: RecordData) -> RecordRead:
        """Update data on a finished record.

        Args:
            record_id: Record ID
            data: Record data to merge

        Returns:
            Updated record
        """
        response = await self._request(
            "PATCH",
            f"/records/{record_id}/data",
            json=data,
        )
        return RecordRead.model_validate(response.json())

    async def prefill_record_data(
        self,
        record_id: int,
        data: RecordData,
        *,
        method: Literal["POST", "PUT", "PATCH"] = "POST",
    ) -> RecordRead:
        """Write prefill data to a pending/blocked record without triggering flows.

        Args:
            record_id: Record ID.
            data: Prefill data.
            method: POST (error if data exists), PUT (replace), PATCH (merge).

        Returns:
            Updated record.
        """
        response = await self._request(
            method,
            f"/records/{record_id}/data/prefill",
            json=data,
        )
        return RecordRead.model_validate(response.json())

    async def update_record(self, record_id: int, **fields: Any) -> RecordRead:
        """Update record fields (partial update).

        Args:
            record_id: Record ID
            **fields: Fields to update (e.g. viewer_study_uids=["1.2.3", "1.2.4"])

        Returns:
            Updated record
        """
        response = await self._request("PATCH", f"/records/{record_id}", json=fields)
        return RecordRead.model_validate(response.json())

    async def update_record_context_info(
        self, record_id: int, context_info: str | None
    ) -> RecordRead:
        """Replace context_info (markdown source) on a record.

        Args:
            record_id: Record ID.
            context_info: Markdown text, or ``None`` to clear. Max 3000 chars.

        Returns:
            Updated record (response includes ``context_info_html``).
        """
        response = await self._request(
            "PATCH",
            f"/records/{record_id}/context-info",
            json={"context_info": context_info},
        )
        return RecordRead.model_validate(response.json())

    async def invalidate_record(
        self,
        record_id: int,
        mode: str = "hard",
        source_record_id: int | None = None,
        reason: str | None = None,
    ) -> RecordRead:
        """Invalidate a record.

        Args:
            record_id: Record ID to invalidate.
            mode: "hard" resets to pending, "soft" only appends reason.
            source_record_id: ID of the record that triggered invalidation.
            reason: Human-readable reason for invalidation.

        Returns:
            Updated record.
        """
        body: dict[str, Any] = {"mode": mode}
        if source_record_id is not None:
            body["source_record_id"] = source_record_id
        if reason is not None:
            body["reason"] = reason

        response = await self._request(
            "POST",
            f"/records/{record_id}/invalidate",
            json=body,
        )
        return RecordRead.model_validate(response.json())

    async def assign_record_to_user(self, record_id: int, user_id: UUID) -> RecordRead:
        """Assign record to a user.

        Args:
            record_id: Record ID
            user_id: User ID to assign to

        Returns:
            Updated record
        """
        response = await self._request(
            "PATCH",
            f"/records/{record_id}/user",
            params={"user_id": str(user_id)},
        )
        return RecordRead.model_validate(response.json())

    async def submit_record_data(
        self,
        record_id: int,
        data: RecordData,
        *,
        status: RecordStatus | None = None,
    ) -> RecordRead:
        """Submit data for a record.

        Args:
            record_id: Record ID
            data: Record data
            status: Target status (default ``finished``).
                Pass ``RecordStatus.failed`` to mark the record as failed
                without triggering downstream flows.

        Returns:
            Updated record
        """
        params = {"status": status.value} if status is not None else None
        response = await self._request(
            "POST",
            f"/records/{record_id}/data",
            json=data,
            params=params,
        )
        return RecordRead.model_validate(response.json())

    async def check_record_files(self, record_id: int) -> dict[str, Any]:
        """Check record files for changes and compute checksums.

        Args:
            record_id: Record ID

        Returns:
            Dict with 'changed_files' and 'checksums' keys
        """
        response = await self._request(
            "POST",
            f"/records/{record_id}/check-files",
        )
        result: dict[str, Any] = response.json()
        return result

    async def notify_file_changes(
        self, patient_id: str, changed_files: list[str]
    ) -> dict[str, Any]:
        """Notify the API that project-level files have changed.

        Args:
            patient_id: The patient whose files changed.
            changed_files: List of logical file names that changed.

        Returns:
            Response dict with dispatched file names.
        """
        response = await self._request(
            "POST",
            f"/patients/{patient_id}/file-events",
            json=changed_files,
        )
        result: dict[str, Any] = response.json()
        return result

    async def find_records_page(
        self,
        cursor: str | None = None,
        limit: int = 100,
        sort: str = "changed_at_desc",
        **filters: Any,
    ) -> RecordPage:
        """Find records with cursor-based pagination.

        Returns:
            RecordPage with items, next_cursor, limit, sort
        """
        body = {k: v for k, v in filters.items() if v is not None}
        body["cursor"] = cursor
        body["limit"] = limit
        body["sort"] = sort
        response = await self._request("POST", "/records/find", json=body)
        return RecordPage.model_validate(response.json())

    async def find_records(
        self,
        skip: int = 0,
        limit: int = 100,
        **filters: Any,
    ) -> list[RecordRead]:
        """Legacy wrapper — returns first page items only."""
        if skip:
            logger.warning("find_records.skip is ignored after cursor migration")
        page = await self.find_records_page(limit=limit, **filters)
        return page.items

    async def find_random_record(self, **filters: Any) -> RecordRead | None:
        """Find a single random record matching filters."""
        body = {k: v for k, v in filters.items() if v is not None}
        response = await self._request("POST", "/records/find/random", json=body)
        data = response.json()
        return RecordRead.model_validate(data) if data is not None else None

    async def iter_records(
        self,
        batch: int = 500,
        sort: str = "changed_at_desc",
        **filters: Any,
    ) -> AsyncIterator[RecordRead]:
        """Stream all matching records through cursor pages."""
        cursor: str | None = None
        while True:
            page = await self.find_records_page(cursor=cursor, limit=batch, sort=sort, **filters)
            for record in page.items:
                yield record
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    # ==================== High-Level Convenience Methods ====================

    async def create_studies_batch(
        self, studies_data: list[dict[str, Any] | StudyCreate]
    ) -> list[Study]:
        """Create multiple studies at once.

        Args:
            studies_data: List of study data dictionaries or models

        Returns:
            List of created studies

        Example:
            ```python
            studies = await client.create_studies_batch(
                [
                    {"study_uid": "1.2.3", "date": "2024-01-01", "patient_id": "P001"},
                    {"study_uid": "1.2.4", "date": "2024-01-02", "patient_id": "P002"},
                ]
            )
            ```
        """
        created_studies: list[Study] = []
        for study_data in studies_data:
            try:
                study = await self.create_study(study_data)
                created_studies.append(study)
                logger.info(f"Created study: {study.study_uid}")
            except ClarinetAPIError as e:
                logger.error(f"Failed to create study {study_data}: {e}")
                # Continue with other studies
                continue

        return created_studies

    async def create_patient_with_studies(
        self,
        patient_data: dict[str, Any] | PatientSave,
        studies_data: list[dict[str, Any] | StudyCreate],
    ) -> tuple[Patient, list[Study]]:
        """Create a patient and associated studies in one operation.

        Args:
            patient_data: Patient data
            studies_data: List of study data (will be linked to created patient)

        Returns:
            Tuple of (created patient, list of created studies)

        Example:
            ```python
            patient, studies = await client.create_patient_with_studies(
                patient_data={"id": "P001", "name": "John Doe"},
                studies_data=[
                    {"study_uid": "1.2.3", "date": "2024-01-01"},
                    {"study_uid": "1.2.4", "date": "2024-01-02"},
                ],
            )
            ```
        """
        # Create patient first
        patient = await self.create_patient(patient_data)
        logger.info(f"Created patient: {patient.id}")

        # Create studies linked to this patient
        studies = []
        for study_data in studies_data:
            if isinstance(study_data, dict):
                # Ensure patient_id is set
                study_data["patient_id"] = patient.id
            else:
                study_data.patient_id = patient.id

            try:
                study = await self.create_study(study_data)
                studies.append(study)
                logger.info(f"Created study {study.study_uid} for patient {patient.id}")
            except ClarinetAPIError as e:
                logger.error(f"Failed to create study for patient {patient.id}: {e}")
                continue

        return patient, studies

    async def assign_records_bulk(self, record_ids: list[int], user_id: UUID) -> list[RecordRead]:
        """Assign multiple records to a user at once.

        Args:
            record_ids: List of record IDs
            user_id: User ID to assign records to

        Returns:
            List of updated records

        Example:
            ```python
            records = await client.assign_records_bulk([1, 2, 3], user_id)
            ```
        """
        updated_records: list[RecordRead] = []
        for record_id in record_ids:
            try:
                record = await self.assign_record_to_user(record_id, user_id)
                updated_records.append(record)
                logger.info(f"Assigned record {record_id} to user {user_id}")
            except ClarinetAPIError as e:
                logger.error(f"Failed to assign record {record_id}: {e}")
                continue

        return updated_records

    async def get_study_hierarchy(self, study_uid: str) -> dict[str, Any]:
        """Get complete study hierarchy including patient, series, and records.

        Args:
            study_uid: Study UID

        Returns:
            Dictionary with study, patient, series, and records data

        Example:
            ```python
            hierarchy = await client.get_study_hierarchy("1.2.3.4.5")
            print(hierarchy["patient"]["name"])
            print(f"Series count: {len(hierarchy['series'])}")
            ```
        """
        # Get study
        study = await self.get_study(study_uid)

        # Get patient
        patient = await self.get_patient(study.patient_id)

        # Get series
        series_list = await self.get_study_series(study_uid)

        # Get records for this study
        records = await self.find_records(study_uid=study_uid, limit=1000)

        return {
            "study": study.model_dump(),
            "patient": patient.model_dump(),
            "series": [s.model_dump() for s in series_list],
            "records": [t.model_dump() for t in records],
        }

    async def find_records_advanced(
        self,
        patient_id: str | None = None,
        patient_anon_id: str | None = None,
        series_uid: str | None = None,
        study_uid: str | None = None,
        user_id: UUID | None = None,
        record_type_name: str | None = None,
        record_status: RecordStatus | None = None,
        wo_user: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[RecordRead]:
        """Advanced record search with multiple filter options."""
        if skip:
            logger.warning("find_records_advanced.skip is ignored after cursor migration")
        return await self.find_records(
            limit=limit,
            patient_id=patient_id,
            patient_anon_id=patient_anon_id,
            series_uid=series_uid,
            study_uid=study_uid,
            user_id=str(user_id) if user_id else None,
            record_type_name=record_type_name,
            record_status=record_status.value if record_status else None,
            wo_user=wo_user,
        )

    async def get_pipeline_definition(self, name: str) -> list[dict[str, str]]:
        """Get pipeline definition steps by name.

        Args:
            name: Pipeline name.

        Returns:
            Ordered list of step dicts with ``task_name`` and ``queue`` keys.

        Raises:
            ClarinetAPIError: If the pipeline is not found or the request fails.
        """
        response = await self._request("GET", f"/pipelines/{name}/definition")
        data: dict[str, Any] = response.json()
        steps: list[dict[str, str]] = data.get("steps", [])
        return steps

    async def create_series_batch(
        self, series_data: list[dict[str, Any] | SeriesCreate]
    ) -> list[Series]:
        """Create multiple series at once.

        Args:
            series_data: List of series data dictionaries or models

        Returns:
            List of created series

        Example:
            ```python
            series = await client.create_series_batch(
                [
                    {"series_uid": "1.2.3.4", "series_number": 1, "study_uid": "1.2.3"},
                    {"series_uid": "1.2.3.5", "series_number": 2, "study_uid": "1.2.3"},
                ]
            )
            ```
        """
        created_series: list[Series] = []
        for series_item in series_data:
            try:
                series = await self.create_series(series_item)
                created_series.append(series)
                logger.info(f"Created series: {series.series_uid}")
            except ClarinetAPIError as e:
                logger.error(f"Failed to create series {series_item}: {e}")
                continue

        return created_series
