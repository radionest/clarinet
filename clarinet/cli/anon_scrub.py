"""CLI entry point for ``clarinet anon scrub-db``.

Anonymizes the configured database in place for the requested patients (the
operator restores a production copy into a throwaway scratch database first),
then optionally ``pg_dump``s the result into a fixture artifact. The scrubbing
logic lives in :mod:`clarinet.services.db_scrub`; this module only wires args,
the session, the dump and the exit code.
"""

import argparse
import gzip
import os
import shutil
import subprocess
import sys

from clarinet.services.db_scrub import DbScrubber, PhiLeakError, ScrubReport
from clarinet.settings import DatabaseDriver, settings
from clarinet.utils.db_manager import db_manager
from clarinet.utils.logger import enable_verbose_console, logger


def _parse_keep(value: str) -> set[str] | None:
    """``"all"`` → keep every patient (None sentinel); else a CSV id set."""
    if value.strip().lower() == "all":
        return None
    ids = {part.strip() for part in value.split(",") if part.strip()}
    if not ids:
        raise ValueError("--patients is empty; pass comma-separated ids or 'all'")
    return ids


async def scrub_db(args: argparse.Namespace) -> None:
    """Run the DB scrubber, then optionally dump the result.

    Exits the process with status 1 on a PHI-audit failure or an empty/invalid
    selection, so CI and operators get a non-zero signal.
    """
    if args.verbose:
        enable_verbose_console()

    if args.out and not args.dry_run:
        _validate_dump_target()  # fail fast before mutating the DB

    keep = _parse_keep(args.patients)
    async with db_manager.async_session_factory() as session:
        scrubber = DbScrubber(
            session,
            keep_patient_ids=keep,
            dry_run=args.dry_run,
            allow_phi_leak=args.allow_phi_leak,
        )
        try:
            report = await scrubber.run()
        except (PhiLeakError, ValueError) as exc:
            logger.error(f"scrub-db failed: {exc}")
            sys.exit(1)

    _log_report(report)

    if args.out and report.committed:
        _pg_dump(args.out)
        logger.info(f"Wrote anonymized dump to {args.out}")
    elif args.out and not report.committed:
        logger.warning("--out skipped: nothing was committed (dry-run or audit failure)")


def _validate_dump_target() -> None:
    """Fail fast (before any DB mutation) if ``--out`` cannot be produced."""
    if settings.database_driver == DatabaseDriver.SQLITE:
        logger.error("--out requires PostgreSQL (pg_dump); SQLite is not supported")
        sys.exit(1)
    if shutil.which("pg_dump") is None:
        logger.error("--out requested but 'pg_dump' was not found on PATH")
        sys.exit(1)


def _log_report(report: ScrubReport) -> None:
    leak = f", PHI hits={sorted(report.phi_hits)}" if report.phi_hits else ""
    logger.info(
        f"scrub-db done (committed={report.committed}): "
        f"patients kept={report.patients_kept}, deleted={report.patients_deleted}, "
        f"records scrubbed={report.records_scrubbed}, audit events={report.events_scrubbed}, "
        f"users scrubbed={report.users_scrubbed}{leak}"
    )


def _pg_dump(out_path: str) -> None:
    """``pg_dump`` the configured database to ``out_path`` (``.gz`` compresses).

    Caller must ensure the driver is PostgreSQL.
    """
    env = {**os.environ, "PGPASSWORD": settings.database_password.get_secret_value()}
    cmd = [
        "pg_dump",
        "-h",
        settings.database_host,
        "-p",
        str(settings.database_port),
        "-U",
        settings.database_username,
        "-d",
        settings.database_name,
        "--no-owner",
        "--no-privileges",
    ]
    # Buffer then write via a temp file + atomic rename: a pg_dump failure
    # (check=True raises before any file is created) or a mid-write error never
    # leaves a truncated artifact at out_path that looks like a valid dump.
    completed = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, check=True)
    tmp = f"{out_path}.partial"
    opener = gzip.open if out_path.endswith(".gz") else open
    with opener(tmp, "wb") as fh:
        fh.write(completed.stdout)
    os.replace(tmp, out_path)
