"""Helper utilities for tests."""

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session

from src.models.patient import Patient
from src.models.study import Series, Study
from src.models.task import Task, TaskDesign, TaskStatus
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
            id=username or f"testuser_{unique_id}",
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


class TaskFactory:
    """Factory for creating test tasks."""

    @staticmethod
    async def create_task_scheme(
        session: Session,
        name: str | None = None,
        title: str | None = None,
        task_design: str = "CLASSIFICATION",  # Changed from TaskType enum
        schema: dict[str, Any] | None = None,
    ) -> TaskDesign:
        """Creates a task type."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        default_schema = {"type": "object", "properties": {"label": {"type": "string"}}}

        task_scheme = TaskDesign(
            name=name or f"test_scheme_{unique_id}",
            title=title or f"Test Scheme {unique_id}",
            type=task_design,
            schema=json.dumps(schema or default_schema),
        )
        session.add(task_scheme)
        await session.commit()
        await session.refresh(task_scheme)
        return task_scheme

    @staticmethod
    async def create_task(
        session: Session,
        user: User,
        task_scheme: TaskDesign,
        status: TaskStatus = TaskStatus.pending,
        data: dict[str, Any] | None = None,
    ) -> Task:
        """Creates a task."""
        task = Task(
            user_id=user.id,
            task_scheme_id=task_scheme.id,
            status=status,
            data=json.dumps(data) if data else None,
        )

        if status == TaskStatus.finished:
            task.completed_at = datetime.now(UTC)

        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


class PatientFactory:
    """Factory for creating test patients and studies."""

    @staticmethod
    async def create_patient(
        session: Session,
        patient_id: str | None = None,
        patient_name: str | None = None,
        patient_sex: str = "M",
        patient_birthdate: date | None = None,
    ) -> Patient:
        """Creates a patient."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        patient = Patient(
            patient_id=patient_id or f"PAT_{unique_id}",
            patient_name=patient_name or f"Test Patient {unique_id}",
            patient_sex=patient_sex,
            patient_birthdate=patient_birthdate or date(1980, 1, 1),
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
        modality: str = "CT",
        study_description: str | None = None,
    ) -> Study:
        """Creates a study."""
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        study = Study(
            patient_id=patient.id,
            study_instance_uid=study_uid or f"1.2.3.{unique_id}",
            study_date=datetime.now(UTC).date(),
            study_description=study_description or f"Test Study {unique_id}",
            modality=modality,
            accession_number=f"ACC_{unique_id}",
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
            study_id=study.id,
            series_instance_uid=series_uid or f"{study.study_instance_uid}.{series_number}",
            series_number=series_number,
            series_description=series_description or f"Series {series_number}",
            modality=study.modality,
            body_part_examined="CHEST",
        )
        session.add(series)
        await session.commit()
        await session.refresh(series)
        return series


class TestDataGenerator:
    """Generator for complex test data."""

    @staticmethod
    async def create_full_test_environment(session: Session) -> dict[str, Any]:
        """Creates a complete test environment with users, tasks and studies."""
        # Create users
        regular_user = await UserFactory.create_user(
            session, email="regular@test.com", username="regular_user"
        )

        admin_user = await UserFactory.create_user(
            session, email="admin@test.com", username="admin_user", roles=["admin"]
        )

        # Create task types
        classification_scheme = await TaskFactory.create_task_scheme(
            session,
            name="classification",
            title="Classification Task",
            task_design="CLASSIFICATION",
        )

        segmentation_scheme = await TaskFactory.create_task_scheme(
            session,
            name="segmentation",
            title="Segmentation Task",
            task_design="SEGMENTATION",
        )

        # Create tasks
        tasks = []
        for user in [regular_user, admin_user]:
            for scheme in [classification_scheme, segmentation_scheme]:
                task = await TaskFactory.create_task(
                    session,
                    user=user,
                    task_scheme=scheme,
                    status=TaskStatus.PENDING,
                    data={"test": "data"},
                )
                tasks.append(task)

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
            for j in range(2):
                study = await PatientFactory.create_study(
                    session, patient=patient, modality="CT" if j == 0 else "MR"
                )
                studies.append(study)

                # Create 3 series for each study
                for k in range(3):
                    series = await PatientFactory.create_series(
                        session, study=study, series_number=k + 1
                    )
                    series_list.append(series)

        return {
            "users": {"regular": regular_user, "admin": admin_user},
            "task_schemes": {
                "classification": classification_scheme,
                "segmentation": segmentation_scheme,
            },
            "tasks": tasks,
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


async def assert_task_status(session: Session, task_id: int, expected_status: TaskStatus):
    """Checks task status."""
    task = await session.get(Task, task_id)
    assert task is not None, f"Task with id {task_id} not found"
    assert task.status == expected_status, f"Expected status {expected_status}, got {task.status}"


async def count_user_tasks(session: Session, user_id: int) -> int:
    """Counts user tasks."""
    from sqlmodel import func, select

    statement = select(func.count(Task.id)).where(Task.user_id == user_id)
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
