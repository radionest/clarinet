"""Helper utilities for tests."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from src.models.patient import Patient
from src.models.record import Record, RecordStatus, RecordType
from src.models.study import Series, Study
from src.models.user import User, UserRole
from src.utils.auth import get_password_hash


class UserFactory:
    """Factory for creating test users."""

    @staticmethod
    async def create_user(
        session: Session,
        email: str | None = None,
        username: str | None = None,
        password: str = "testpassword",
        is_active: bool = True,
        is_verified: bool = True,
        roles: list[str] | None = None,
    ) -> User:
        """Creates a test user."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        user = User(
            id=uuid.uuid4(),  # Use UUID instead of string
            email=email or f"test_{unique_id}@example.com",
            hashed_password=get_password_hash(password),
            is_active=is_active,
            is_verified=is_verified,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        # Add roles if specified
        if roles:
            from src.models.user import UserRolesLink

            for role_name in roles:
                # First create role if it doesn't exist
                existing_role = await session.get(UserRole, role_name)
                if not existing_role:
                    role_obj = UserRole(name=role_name)
                    session.add(role_obj)
                    await session.commit()

                # Create link between user and role
                link = UserRolesLink(user_id=user.id, role_name=role_name)
                session.add(link)
            await session.commit()

        return user


class RecordFactory:
    """Factory for creating test records."""

    @staticmethod
    async def create_record_type(
        session: Session,
        name: str | None = None,
        title: str | None = None,
        record_type: str = "CLASSIFICATION",  # Changed from TaskType enum
        schema: dict[str, Any] | None = None,
    ) -> RecordType:
        """Creates a record type."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        default_schema = {"type": "object", "properties": {"label": {"type": "string"}}}

        record_type_obj = RecordType(
            name=name or f"test_type_{unique_id}",
            title=title or f"Test Type {unique_id}",
            type=record_type,
            schema=json.dumps(schema or default_schema),
        )
        session.add(record_type_obj)
        await session.commit()
        await session.refresh(record_type_obj)
        return record_type_obj

    @staticmethod
    async def create_record(
        session: Session,
        user: User,
        record_type: RecordType,
        status: RecordStatus = RecordStatus.pending,
        data: dict[str, Any] | None = None,
    ) -> Record:
        """Creates a record."""
        record = Record(
            user_id=user.id,
            record_type_name=record_type.name,
            status=status,
            data=json.dumps(data) if data else None,
        )

        if status == RecordStatus.finished:
            record.finished_at = datetime.now(UTC)

        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record


class PatientFactory:
    """Factory for creating test patients and studies."""

    @staticmethod
    async def create_patient(
        session: Session,
        patient_id: str | None = None,
        patient_name: str | None = None,
    ) -> Patient:
        """Creates a patient."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        patient = Patient(
            id=patient_id or f"PAT_{unique_id}",
            name=patient_name or f"Test Patient {unique_id}",
        )
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
        return patient

    @staticmethod
    async def create_study(
        session: Session,
        patient: Patient,
        study_uid: str | None = None,
    ) -> Study:
        """Creates a study."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        study = Study(
            patient_id=patient.id,
            study_uid=study_uid or f"1.2.3.{unique_id}",
            date=datetime.now(UTC).date(),
        )
        session.add(study)
        await session.commit()
        await session.refresh(study)
        return study

    @staticmethod
    async def create_series(
        session: Session,
        study: Study,
        series_uid: str | None = None,
        series_number: int = 1,
        series_description: str | None = None,
    ) -> Series:
        """Creates a series."""

        series = Series(
            study_uid=study.study_uid,
            series_uid=series_uid or f"{study.study_uid}.{series_number}",
            series_number=series_number,
            series_description=series_description or f"Series {series_number}",
        )
        session.add(series)
        await session.commit()
        await session.refresh(series)
        return series


class TestDataGenerator:
    """Generator for complex test data."""

    @staticmethod
    async def create_full_test_environment(session: Session) -> dict[str, Any]:
        """Creates a complete test environment with users, records and studies."""
        # Create users
        regular_user = await UserFactory.create_user(
            session,
            email="regular@test.com",  # Remove username parameter
        )

        admin_user = await UserFactory.create_user(
            session,
            email="admin@test.com",
            roles=["admin"],  # Remove username parameter
        )

        # Create record types
        classification_type = await RecordFactory.create_record_type(
            session,
            name="classification",
            title="Classification Record",
            record_type="CLASSIFICATION",
        )

        segmentation_type = await RecordFactory.create_record_type(
            session,
            name="segmentation",
            title="Segmentation Record",
            record_type="SEGMENTATION",
        )

        # Create records
        records = []
        for user in [regular_user, admin_user]:
            for record_type in [classification_type, segmentation_type]:
                record = await RecordFactory.create_record(
                    session,
                    user=user,
                    record_type=record_type,
                    status=RecordStatus.pending,
                    data={"test": "data"},
                )
                records.append(record)

        # Create patients and studies
        patients = []
        studies = []
        series_list = []

        for i in range(3):
            patient = await PatientFactory.create_patient(
                session, patient_id=f"TEST_PAT_{i}", patient_name=f"Test Patient {i}"
            )
            patients.append(patient)

            # Create 2 studies for each patient
            for _j in range(2):
                study = await PatientFactory.create_study(session, patient=patient)
                studies.append(study)

                # Create 3 series for each study
                for k in range(3):
                    series = await PatientFactory.create_series(
                        session, study=study, series_number=k + 1
                    )
                    series_list.append(series)

        return {
            "users": {"regular": regular_user, "admin": admin_user},
            "record_types": {
                "classification": classification_type,
                "segmentation": segmentation_type,
            },
            "records": records,
            "patients": patients,
            "studies": studies,
            "series": series_list,
        }


async def assert_user_exists(session: Session, email: str) -> User:
    """Checks if user exists and returns it."""
    from sqlmodel import select

    statement = select(User).where(User.email == email)
    result = await session.exec(statement)
    user = result.first()

    assert user is not None, f"User with email {email} not found"
    return user


async def assert_record_status(session: Session, record_id: int, expected_status: RecordStatus):
    """Checks record status."""
    record = await session.get(Record, record_id)
    assert record is not None, f"Record with id {record_id} not found"
    assert record.status == expected_status, (
        f"Expected status {expected_status}, got {record.status}"
    )


async def count_user_records(session: Session, user_id) -> int:
    """Counts user records."""
    from sqlmodel import func, select

    statement = select(func.count(Record.id)).where(Record.user_id == user_id)
    result = await session.exec(statement)
    return result.one()


async def get_auth_token(client, email: str, password: str) -> str:
    """Gets authorization token for user."""
    response = await client.post(
        "/api/auth/login",
        data={
            "username": email,
            "password": password,
        },
    )
    assert response.status_code == 200, f"Failed to login: {response.text}"
    return response.json()["access_token"]
