"""
Clarinet API Client.

This module provides a Python client for interacting with the Clarinet API,
supporting both low-level API calls and high-level convenience methods.
"""

import getpass
from datetime import date
from typing import Any, cast
from uuid import UUID

import httpx

from src.models import (
    PatientRead,
    PatientSave,
    RecordCreate,
    RecordRead,
    RecordStatus,
    RecordType,
    RecordTypeCreate,
    RecordTypeFind,
    SeriesCreate,
    SeriesFind,
    SeriesRead,
    StudyCreate,
    StudyRead,
    UserRead,
)
from src.types import RecordData
from src.utils.logger import logger

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
        client = ClarinetClient("http://localhost:8000", username="admin")

        # With password provided
        client = ClarinetClient("http://localhost:8000", username="admin", password="secret")

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

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        auto_login: bool = True,
        log_requests: bool = False,
    ) -> None:
        """Initialize Clarinet client.

        Args:
            base_url: Base URL of the Clarinet API (e.g., "http://localhost:8000")
            username: Username for authentication (optional if using existing session)
            password: Password for authentication. If None and username is provided,
                     will prompt for password interactively
            auto_login: Automatically login on initialization (default: True)
            log_requests: Enable request/response logging (default: False)
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.log_requests = log_requests

        # Create async HTTP client with cookie jar for session management
        self.client = httpx.AsyncClient(base_url=self.base_url, follow_redirects=True)
        self._authenticated = False

        # Auto-login if credentials provided
        if auto_login and username:
            # Note: __init__ cannot be async, so we'll login on first request
            # User can also call login() manually
            pass

    async def __aenter__(self) -> "ClarinetClient":
        """Async context manager entry."""
        if self.username and not self._authenticated:
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
        **kwargs: Any,
    ) -> httpx.Response:
        """Make HTTP request to API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/auth/login")
            raise_for_status: Raise exception on HTTP errors
            **kwargs: Additional arguments passed to httpx request

        Returns:
            HTTP response

        Raises:
            ClarinetAPIError: On API errors
            ClarinetAuthError: On authentication errors
        """
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        self._log_request(method, url, **kwargs)

        try:
            response = await self.client.request(method, url, **kwargs)
            self._log_response(response)

            if raise_for_status:
                if response.status_code == 401:
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
        return cast("UserRead", UserRead.model_validate(response.json()))

    async def validate_session(self) -> UserRead:
        """Validate current session.

        Returns:
            Current user if session is valid

        Raises:
            ClarinetAuthError: If session is invalid
        """
        response = await self._request("GET", "/auth/session/validate")
        return cast("UserRead", UserRead.model_validate(response.json()))

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

    async def create_patient(self, patient: PatientSave | dict[str, Any]) -> PatientRead:
        """Create a new patient.

        Args:
            patient: Patient data (model or dict)

        Returns:
            Created patient
        """
        if isinstance(patient, dict):
            patient = PatientSave.model_validate(patient)

        response = await self._request(
            "POST",
            "/patients",
            json=patient.model_dump(by_alias=True, mode="json"),
        )
        return PatientRead.model_validate(response.json())

    async def anonymize_patient(self, patient_id: str) -> PatientRead:
        """Anonymize a patient.

        Args:
            patient_id: Patient ID to anonymize

        Returns:
            Updated patient with anonymous name
        """
        response = await self._request("POST", f"/patients/{patient_id}/anonymize")
        return PatientRead.model_validate(response.json())

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

    async def get_study_series(self, study_uid: str) -> list[SeriesRead]:
        """Get all series for a study.

        Args:
            study_uid: Study UID

        Returns:
            List of series in the study
        """
        response = await self._request("GET", f"/studies/{study_uid}/series")
        return [SeriesRead.model_validate(s) for s in response.json()]

    async def create_study(self, study: StudyCreate | dict[str, Any]) -> StudyRead:
        """Create a new study.

        Args:
            study: Study data (model or dict)

        Returns:
            Created study
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
        return StudyRead.model_validate(response.json())

    async def add_anonymized_study_uid(self, study_uid: str, anon_uid: str) -> StudyRead:
        """Add anonymized UID to a study.

        Args:
            study_uid: Original study UID
            anon_uid: Anonymized study UID

        Returns:
            Updated study
        """
        response = await self._request(
            "POST",
            f"/studies/{study_uid}/add_anonymized",
            params={"anon_uid": anon_uid},
        )
        return StudyRead.model_validate(response.json())

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

    async def create_series(self, series: SeriesCreate | dict[str, Any]) -> SeriesRead:
        """Create a new series.

        Args:
            series: Series data (model or dict)

        Returns:
            Created series
        """
        if isinstance(series, dict):
            series = SeriesCreate.model_validate(series)

        response = await self._request(
            "POST",
            "/series",
            json=series.model_dump(mode="json"),
        )
        return SeriesRead.model_validate(response.json())

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

    async def add_anonymized_series_uid(self, series_uid: str, anon_uid: str) -> SeriesRead:
        """Add anonymized UID to a series.

        Args:
            series_uid: Original series UID
            anon_uid: Anonymized series UID

        Returns:
            Updated series
        """
        response = await self._request(
            "POST",
            f"/series/{series_uid}/add_anonymized",
            params={"anon_uid": anon_uid},
        )
        return SeriesRead.model_validate(response.json())

    # ==================== Record Type Management ====================

    async def get_record_types(self) -> list[RecordType]:
        """Get all record types.

        Returns:
            List of all record types
        """
        response = await self._request("GET", "/records/types")
        return [RecordType.model_validate(t) for t in response.json()]

    async def get_record_type(self, record_type_id: int) -> RecordType:
        """Get record type by ID.

        Args:
            record_type_id: Record type ID

        Returns:
            Record type data
        """
        response = await self._request("GET", f"/records/types/{record_type_id}")
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

    async def get_records(self) -> list[RecordRead]:
        """Get all records.

        Returns:
            List of all records
        """
        response = await self._request("GET", "/records/")
        return [RecordRead.model_validate(t) for t in response.json()]

    async def get_my_records(self) -> list[RecordRead]:
        """Get records assigned to current user.

        Returns:
            List of user's records
        """
        response = await self._request("GET", "/records/my")
        return [RecordRead.model_validate(t) for t in response.json()]

    async def get_my_pending_records(self) -> list[RecordRead]:
        """Get pending records assigned to current user.

        Returns:
            List of user's pending records
        """
        response = await self._request("GET", "/records/my/pending")
        return [RecordRead.model_validate(t) for t in response.json()]

    async def get_record(self, record_id: int, detailed: bool = False) -> RecordRead:
        """Get record by ID.

        Args:
            record_id: Record ID
            detailed: Return detailed record info

        Returns:
            Record data
        """
        response = await self._request(
            "GET",
            f"/records/{record_id}",
            params={"detailed": detailed},
        )
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

    async def submit_record_data(self, record_id: int, data: RecordData) -> RecordRead:
        """Submit data for a record.

        Args:
            record_id: Record ID
            data: Record data

        Returns:
            Updated record
        """
        response = await self._request(
            "POST",
            f"/records/{record_id}/data",
            json=data,
        )
        return RecordRead.model_validate(response.json())

    async def find_records(
        self,
        skip: int = 0,
        limit: int = 100,
        **filters: Any,
    ) -> list[RecordRead]:
        """Find records by various criteria.

        Args:
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return
            **filters: Additional filter parameters (patient_id, study_uid, etc.)

        Returns:
            List of matching records
        """
        params = {"skip": skip, "limit": limit}
        params.update(filters)

        response = await self._request("POST", "/records/find", params=params)
        return [RecordRead.model_validate(t) for t in response.json()]

    # ==================== High-Level Convenience Methods ====================

    async def create_studies_batch(
        self, studies_data: list[dict[str, Any] | StudyCreate]
    ) -> list[StudyRead]:
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
        created_studies: list[StudyRead] = []
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
    ) -> tuple[PatientRead, list[StudyRead]]:
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
        """Advanced record search with multiple filter options.

        Args:
            patient_id: Filter by patient ID
            patient_anon_id: Filter by anonymized patient ID
            series_uid: Filter by series UID
            study_uid: Filter by study UID
            user_id: Filter by assigned user
            record_type_name: Filter by record type name
            record_status: Filter by record status
            wo_user: Filter by records without user (True) or with user (False)
            skip: Pagination offset
            limit: Maximum results

        Returns:
            List of matching records
        """
        filters = {
            "patient_id": patient_id,
            "patient_anon_id": patient_anon_id,
            "series_uid": series_uid,
            "study_uid": study_uid,
            "user_id": str(user_id) if user_id else None,
            "record_type_name": record_type_name,
            "record_status": record_status.value if record_status else None,
            "wo_user": wo_user,
        }

        # Remove None values
        filters = {k: v for k, v in filters.items() if v is not None}

        return await self.find_records(skip=skip, limit=limit, **filters)

    async def create_series_batch(
        self, series_data: list[dict[str, Any] | SeriesCreate]
    ) -> list[SeriesRead]:
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
        created_series: list[SeriesRead] = []
        for series_item in series_data:
            try:
                series = await self.create_series(series_item)
                created_series.append(series)
                logger.info(f"Created series: {series.series_uid}")
            except ClarinetAPIError as e:
                logger.error(f"Failed to create series {series_item}: {e}")
                continue

        return created_series
