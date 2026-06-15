"""In-place anonymization of a Clarinet database for test-stand fixtures.

:class:`DbScrubber` turns a restored copy of a live project database into an
anonymized fixture. It narrows the database to the requested patients, strips
PHI from every relational column and JSON snapshot, rewrites the patient MRN to
the deterministic ``anon_id`` — and preserves exactly the values
``FileRepository`` needs to resolve the anonymized DICOM on the stand
(``patient.auto_id``, ``study.anon_uid``, ``series.anon_uid``). A final audit
scan fails the run if any captured name or MRN survives.

Consistency with DICOM anonymization (``services/dicom/anonymizer.py``):
``patient.id`` becomes ``f"{anon_id_prefix}_{auto_id}"`` — identical to
:pyattr:`Patient.anon_id` and to the ``PatientID`` tag written into
``dcm_anon`` in per-patient mode. The raw ``study_uid`` / ``series_uid`` PKs
are kept: DICOM UIDs are not classic PHI, per-study PatientID derivation hashes
the raw ``study_uid``, and the anonymized UID columns that drive the on-disk
path are left untouched. ``study.date`` is kept (needed by ``{study_date}``
templates; low risk alongside anonymized identifiers).

Operates on the database in ``settings`` (the operator restores a production
copy into a throwaway scratch database first) inside a single transaction.
``dry_run`` rolls the transaction back after reporting.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from clarinet.models.auth import AccessToken
from clarinet.models.file_schema import RecordFileLink
from clarinet.models.patient import Patient
from clarinet.models.pipeline_task_run import PipelineTaskRun
from clarinet.models.record import Record
from clarinet.models.record_event import RecordEvent
from clarinet.models.record_type import RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from clarinet.services.db_scrub.audit import collect_phi_terms, scan_json, scan_text
from clarinet.services.db_scrub.json_scrub import scrub_record_data
from clarinet.settings import DatabaseDriver, settings
from clarinet.utils.logger import logger

# Tables whose ``patient_id`` FK is repointed during the MRN→anon_id PK rewrite.
# Hardcoded against the framework schema (stable). ``record_event`` has no
# ``patient_id`` (it links via ``record_id``), so it is not listed here.
_PATIENT_FK_MODELS = (Study, Record, PipelineTaskRun)

# Placeholder for ``User.hashed_password``: real bcrypt hashes are crackable
# credentials and must not ship in a fixture. Not a valid hash, so password
# verification fails closed; the stand re-establishes admin login on load.
_NO_LOGIN_HASH = "!scrubbed-no-login!"


class PhiLeakError(RuntimeError):
    """Audit found captured PHI surviving in the scrubbed database."""


@dataclass
class ScrubReport:
    """Outcome of a scrub run (counts + audit result)."""

    patients_kept: int = 0
    patients_deleted: int = 0
    records_scrubbed: int = 0
    events_scrubbed: int = 0
    users_scrubbed: int = 0
    phi_hits: set[str] = field(default_factory=set)
    committed: bool = False


@dataclass
class _PatientRow:
    """Pre-mutation snapshot of a kept patient (drives the PK rewrite)."""

    old_id: str
    auto_id: int
    name: str | None

    @property
    def new_id(self) -> str:
        # Per-patient anon id even in per-study mode: this is the patient PK,
        # not the per-study DICOM PatientID. Matches ``Patient.anon_id``.
        return f"{settings.anon_id_prefix}_{self.auto_id}"

    @property
    def scrubbed_name(self) -> str:
        return f"Patient_{self.auto_id}"

    @property
    def final_anon_name(self) -> str:
        # Regenerate deterministically instead of preserving the original
        # anon_name: a previously-set alias could itself carry identifying
        # text that the term-based audit would not flag. Matches the
        # ``{prefix}_{auto_id}`` fallback in StudyService.anonymize_patient.
        return f"{settings.anon_id_prefix}_{self.auto_id}"


class DbScrubber:
    """Anonymize the configured database in place for the given patients."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        keep_patient_ids: set[str] | None,
        dry_run: bool = False,
        allow_phi_leak: bool = False,
    ) -> None:
        """``keep_patient_ids=None`` keeps every patient (scrub-only, no subset)."""
        self.session = session
        self.keep_patient_ids = keep_patient_ids
        self.dry_run = dry_run
        self.allow_phi_leak = allow_phi_leak

    async def run(self) -> ScrubReport:
        """Execute the full scrub pipeline, returning a :class:`ScrubReport`.

        Raises:
            PhiLeakError: the audit found surviving PHI and ``allow_phi_leak``
                is False (the transaction is rolled back first).
            ValueError: the requested patient selection is empty.
        """
        patients = await self._load_kept_patients()
        if not patients:
            raise ValueError("no patients selected — refusing to scrub an empty set")
        keep_ids = {p.old_id for p in patients}
        phi_terms = collect_phi_terms([p.name for p in patients], [p.old_id for p in patients])
        report = ScrubReport(patients_kept=len(patients))

        total = await self._scalar(select(func.count()).select_from(Patient))
        report.patients_deleted = int(total or 0) - len(patients)

        await self._delete_out_of_scope(keep_ids)
        schema_map = await self._schema_map()
        report.records_scrubbed = await self._scrub_records(schema_map)
        id_map = {p.old_id: p.new_id for p in patients}
        report.events_scrubbed = await self._scrub_audit_tables(schema_map, id_map)
        report.users_scrubbed = await self._scrub_users()
        await self._scrub_relational_text()
        await self._rewrite_patient_ids(patients)
        await self._fix_auto_id_counter()

        report.phi_hits = await self._audit(phi_terms)

        if report.phi_hits and not self.allow_phi_leak:
            await self.session.rollback()
            raise PhiLeakError(
                f"audit found {len(report.phi_hits)} PHI term(s) surviving the "
                f"scrub: {sorted(report.phi_hits)} — rolled back"
            )
        if self.dry_run:
            await self.session.rollback()
            logger.info("dry-run: rolled back, no changes persisted")
        else:
            await self.session.commit()
            report.committed = True
        return report

    # ── selection ─────────────────────────────────────────────────────

    async def _load_kept_patients(self) -> list[_PatientRow]:
        stmt = select(Patient.id, Patient.auto_id, Patient.name)
        if self.keep_patient_ids is not None:
            stmt = stmt.where(col(Patient.id).in_(self.keep_patient_ids))
        rows = (await self.session.execute(stmt)).all()
        if self.keep_patient_ids is not None:
            # Count only — the ids are operator-supplied PHI (MRNs); keep them
            # out of logs per .claude/rules/logging-pii.md.
            missing = len(self.keep_patient_ids - {r[0] for r in rows})
            if missing:
                logger.warning(f"{missing} requested patient id(s) not found; skipping")
        return [_PatientRow(old_id=r[0], auto_id=r[1], name=r[2]) for r in rows]

    async def _delete_out_of_scope(self, keep_ids: set[str]) -> None:
        """Delete every patient outside ``keep_ids`` and their dependent rows.

        Explicit child-first deletes (not DB cascade): SQLite enforces FK
        cascade only with ``PRAGMA foreign_keys=ON``, which the engine does not
        set — explicit order is correct on both backends. Orphaned audit rows
        (``record_id``/``patient_id`` NULL) are dropped too: they reference
        already-deleted records and cannot be scoped, so they may carry PHI.
        """
        oos_records = select(Record.id).where(col(Record.patient_id).notin_(keep_ids))
        oos_studies = select(Study.study_uid).where(col(Study.patient_id).notin_(keep_ids))

        await self.session.execute(
            delete(RecordEvent).where(
                col(RecordEvent.record_id).is_(None) | col(RecordEvent.record_id).in_(oos_records)
            )
        )
        await self.session.execute(
            delete(PipelineTaskRun).where(
                col(PipelineTaskRun.patient_id).is_(None)
                | col(PipelineTaskRun.patient_id).notin_(keep_ids)
            )
        )
        await self.session.execute(
            delete(RecordFileLink).where(col(RecordFileLink.record_id).in_(oos_records))
        )
        await self.session.execute(delete(Record).where(col(Record.patient_id).notin_(keep_ids)))
        await self.session.execute(delete(Series).where(col(Series.study_uid).in_(oos_studies)))
        await self.session.execute(delete(Study).where(col(Study.patient_id).notin_(keep_ids)))
        await self.session.execute(delete(Patient).where(col(Patient.id).notin_(keep_ids)))
        # Auth tokens are server-session state — meaningless on a fresh stand.
        await self.session.execute(delete(AccessToken))

    # ── JSON / text scrubbing ─────────────────────────────────────────

    async def _schema_map(self) -> dict[str, dict[str, Any]]:
        rows = (await self.session.execute(select(RecordType.name, RecordType.data_schema))).all()
        return {name: schema for name, schema in rows if isinstance(schema, dict)}

    async def _scrub_records(self, schema_map: dict[str, dict[str, Any]]) -> int:
        rows = (
            await self.session.execute(select(Record.id, Record.data, Record.record_type_name))
        ).all()
        scrubbed = 0
        for rid, data, rt_name in rows:
            new = scrub_record_data(data, schema_map.get(rt_name))
            if new != data:
                await self.session.execute(
                    update(Record).where(col(Record.id) == rid).values(data=new)
                )
                scrubbed += 1
        return scrubbed

    async def _scrub_audit_tables(
        self, schema_map: dict[str, dict[str, Any]], id_map: dict[str, str]
    ) -> int:
        """Scrub embedded record snapshots and free-text of remaining audit rows."""
        rows = (
            await self.session.execute(
                select(RecordEvent.id, RecordEvent.old_value, RecordEvent.new_value)
            )
        ).all()
        for eid, old, new in rows:
            await self.session.execute(
                update(RecordEvent)
                .where(col(RecordEvent.id) == eid)
                .values(
                    old_value=_scrub_snapshot(old, schema_map, id_map),
                    new_value=_scrub_snapshot(new, schema_map, id_map),
                    reason=None,
                )
            )
        # error_message / result may carry file paths or tracebacks with PHI.
        await self.session.execute(update(PipelineTaskRun).values(error_message=None, result=None))
        return len(rows)

    async def _scrub_users(self) -> int:
        """Blank every password hash; scrub non-superuser emails.

        ``hashed_password`` is a real credential and is replaced for ALL users
        regardless of role (the stand re-establishes admin login via
        ``clarinet db init`` / ``admin reset-password``). Superuser emails are
        kept (system accounts such as the default admin) — operators must ensure
        those carry no PII.
        """
        rows = (await self.session.execute(select(User.id, User.is_superuser))).all()
        for uid, is_superuser in rows:
            values: dict[str, Any] = {"hashed_password": _NO_LOGIN_HASH}
            if not is_superuser:
                values["email"] = f"user-{uid}@example.invalid"  # full uuid — unique
            await self.session.execute(update(User).where(col(User.id) == uid).values(**values))
        return len(rows)

    async def _scrub_relational_text(self) -> None:
        """Null the free-text columns outside ``record.data`` / user email.

        ``clarinet_storage_path`` is nulled because it embeds the old MRN;
        ``FileRepository`` recomputes it from the preserved anon identifiers.
        """
        await self.session.execute(update(Study).values(study_description=None))
        await self.session.execute(update(Series).values(series_description=None))
        await self.session.execute(
            update(Record).values(context_info=None, clarinet_storage_path=None)
        )

    # ── patient PK rewrite (MRN → anon_id) ────────────────────────────

    async def _rewrite_patient_ids(self, patients: list[_PatientRow]) -> None:
        """Rewrite each ``patient.id`` (MRN) to ``anon_id``, repointing FKs.

        FK-safe, privilege-free, portable across PostgreSQL and SQLite (no
        ``ON UPDATE CASCADE`` / deferrable constraints exist). Per patient:
        insert the anon row (temporary unique ``auto_id`` + null ``anon_name``
        to dodge the unique constraints), repoint the children, drop the old
        row, then restore the real ``auto_id`` and ``anon_name``.
        """
        base = max((p.auto_id for p in patients), default=0)
        for offset, p in enumerate(patients, start=1):
            if p.new_id == p.old_id:
                continue
            tmp_auto = base + offset
            await self.session.execute(
                insert(Patient).values(
                    id=p.new_id, auto_id=tmp_auto, name=p.scrubbed_name, anon_name=None
                )
            )
            for model in _PATIENT_FK_MODELS:
                await self.session.execute(
                    update(model)
                    .where(col(model.patient_id) == p.old_id)
                    .values(patient_id=p.new_id)
                )
            await self.session.execute(delete(Patient).where(col(Patient.id) == p.old_id))
            await self.session.execute(
                update(Patient)
                .where(col(Patient.id) == p.new_id)
                .values(auto_id=p.auto_id, anon_name=p.final_anon_name)
            )

    async def _fix_auto_id_counter(self) -> None:
        """Pin the monotonic patient counter to ``MAX(auto_id)``.

        Otherwise the stand's first new patient reuses an ``auto_id`` and
        collides on the derived ``anon_id``. PostgreSQL: native sequence;
        SQLite: the ``auto_id_counter`` fallback row.
        """
        max_auto = int(await self._scalar(select(func.coalesce(func.max(Patient.auto_id), 0))) or 0)
        if settings.database_driver != DatabaseDriver.SQLITE:
            await self.session.execute(
                text("SELECT setval('patient_auto_id_seq', :v)"),
                {"v": max(max_auto, 1)},
            )
            return
        existing = await self._scalar(
            text("SELECT last_value FROM auto_id_counter WHERE name = 'patient_auto_id'")
        )
        if existing is None:
            await self.session.execute(
                text(
                    "INSERT INTO auto_id_counter (name, last_value) VALUES ('patient_auto_id', :v)"
                ),
                {"v": max_auto},
            )
        else:
            await self.session.execute(
                text("UPDATE auto_id_counter SET last_value = :v WHERE name = 'patient_auto_id'"),
                {"v": max_auto},
            )

    # ── audit ─────────────────────────────────────────────────────────

    async def _audit(self, terms: set[str]) -> set[str]:
        """Scan every text/JSON column for surviving PHI; log + return hits."""
        if not terms:
            return set()
        hits: set[str] = set()

        def record(table: str, found: set[str]) -> None:
            if found:
                logger.error(f"PHI audit: {sorted(found)} survived in {table}")
                hits.update(found)

        for pid, name, anon in (
            await self.session.execute(select(Patient.id, Patient.name, Patient.anon_name))
        ).all():
            record(
                "patient", scan_text(pid, terms) | scan_text(name, terms) | scan_text(anon, terms)
            )
        for (desc,) in (await self.session.execute(select(Study.study_description))).all():
            record("study", scan_text(desc, terms))
        for (desc,) in (await self.session.execute(select(Series.series_description))).all():
            record("series", scan_text(desc, terms))
        for ctx, path in (
            await self.session.execute(select(Record.context_info, Record.clarinet_storage_path))
        ).all():
            record("record", scan_text(ctx, terms) | scan_text(path, terms))
        # JSON columns scanned one-per-select: a 5-column union select overflows
        # mypy's overload resolution ("too many unions").
        for (data,) in (await self.session.execute(select(Record.data))).all():
            record("record", scan_json(data, terms))
        for (vstudy,) in (await self.session.execute(select(Record.viewer_study_uids))).all():
            record("record", scan_json(vstudy, terms))
        for (vseries,) in (await self.session.execute(select(Record.viewer_series_uids))).all():
            record("record", scan_json(vseries, terms))
        for old, new, reason in (
            await self.session.execute(
                select(RecordEvent.old_value, RecordEvent.new_value, RecordEvent.reason)
            )
        ).all():
            record(
                "record_event",
                scan_json(old, terms) | scan_json(new, terms) | scan_text(reason, terms),
            )
        for msg, result in (
            await self.session.execute(
                select(PipelineTaskRun.error_message, PipelineTaskRun.result)
            )
        ).all():
            record("pipeline_task_run", scan_text(msg, terms) | scan_json(result, terms))
        for (email,) in (await self.session.execute(select(User.email))).all():
            record("user", scan_text(email, terms))
        return hits

    async def _scalar(self, stmt: Any) -> Any:
        return (await self.session.execute(stmt)).scalar()


def _scrub_snapshot(
    snapshot: dict[str, Any] | None,
    schema_map: dict[str, dict[str, Any]],
    id_map: dict[str, str],
) -> dict[str, Any] | None:
    """Scrub a Record snapshot embedded in a ``record_event`` JSON column."""
    if not snapshot:
        return snapshot
    out = dict(snapshot)
    if "data" in out:
        rt_name = out.get("record_type_name")
        schema = schema_map.get(rt_name) if isinstance(rt_name, str) else None
        out["data"] = scrub_record_data(out["data"], schema)
    if "context_info" in out:
        out["context_info"] = None
    if "clarinet_storage_path" in out:
        out["clarinet_storage_path"] = None
    old_pid = out.get("patient_id")
    if isinstance(old_pid, str) and old_pid in id_map:
        out["patient_id"] = id_map[old_pid]
    return out
