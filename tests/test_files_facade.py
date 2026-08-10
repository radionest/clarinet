from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clarinet.models.base import DicomQueryLevel


@pytest.mark.asyncio
async def test_checksum_missing_returns_none(tmp_path):
    from clarinet.files._checksums import checksums_changed, compute_file_checksum

    assert await compute_file_checksum(tmp_path / "nope.bin") is None
    assert checksums_changed({"a": "1"}, {"a": "2", "b": "9"}) == {"a", "b"}


def test_leaf_modules_import():
    from clarinet.files._template import render_template, validate_template

    assert render_template("{a}", {"a": "x"}) == "x"
    assert validate_template("{patient_id}/{study_uid}/{series_uid}")


def test_storage_render_all_levels_smoke(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock

    from clarinet.files import _storage
    from clarinet.models.base import DicomQueryLevel

    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = _storage.render_all_levels(
        patient=patient,
        study=None,
        series=None,
        storage_path=Path("/data"),
        template="{anon_patient_id}/{study_uid}/{series_uid}",
    )
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")


def test_resolver_build_working_dirs(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock

    from clarinet.files import _resolver
    from clarinet.models.base import DicomQueryLevel

    monkeypatch.setattr(
        "clarinet.files._resolver.settings",
        MagicMock(
            storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"
        ),
    )

    record = MagicMock()
    record.clarinet_storage_path = None
    record.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    record.study = None
    record.study_uid = None
    record.series = None
    record.series_uid = None
    dirs = _resolver.build_working_dirs(record)
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")


def _record(monkeypatch, *, registry=None, level="SERIES"):
    monkeypatch.setattr(
        "clarinet.files._resolver.settings",
        MagicMock(
            storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"
        ),
    )
    r = MagicMock()
    r.clarinet_storage_path = None
    r.id = 7
    r.user_id = "u1"
    r.patient_id = "P1"
    r.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    r.study = MagicMock(study_uid="S", anon_uid="S")
    r.study_uid = "S"
    r.series = MagicMock(series_uid="SE", anon_uid="SE", modality="CT", series_number=1)
    r.series_uid = "SE"
    r.record_type = MagicMock(level=level, file_registry=registry or [])
    r.record_type.name = "seg"
    r.data = {}
    # make isinstance(r, RecordRead) true:
    from clarinet.models.record import RecordRead

    r.__class__ = RecordRead
    return r


def test_files_dir_and_dirs(monkeypatch):
    from clarinet.files.facade import Files

    f = Files(_record(monkeypatch))
    assert f.dir() == Path("/data/CLARINET_1/S/SE")
    assert f.dir(DicomQueryLevel.PATIENT) == Path("/data/CLARINET_1")
    assert set(f.dirs()) == {DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY, DicomQueryLevel.SERIES}


def test_files_rejects_bad_type():
    from clarinet.files.facade import Files

    with pytest.raises(TypeError):
        Files(object())


def test_files_empty():
    from clarinet.files.facade import Files

    assert Files.empty().dirs() == {}


def test_files_resolve(monkeypatch):
    from clarinet.files.facade import Files

    fd = MagicMock(name="fd")
    fd.name = "seg"
    fd.pattern = "seg_{id}.nrrd"
    fd.level = None
    f = Files(_record(monkeypatch, registry=[fd]))
    assert f.resolve("seg") == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")
    assert f.accessed["seg"] == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")


def test_files_render_uses_unified_engine(monkeypatch):
    from clarinet.files.facade import Files

    rec = _record(monkeypatch)
    rec.data = {"mods": ["SR", "CT"]}
    f = Files(rec)
    assert f.render("{data.mods}_{id}") == "CT_SR_7"  # type-aware list coercion


def test_files_render_template_strict_raises():
    from clarinet.files.facade import Files

    with pytest.raises(KeyError):
        Files.render_template("{missing}", {}, strict=True)
    assert Files.render_template("{missing}", {}) == ""


@pytest.mark.asyncio
async def test_files_checksums_omits_missing(monkeypatch):
    from clarinet.files.facade import Files

    fd = MagicMock()
    fd.name = "seg"
    fd.pattern = "seg_{id}.nrrd"
    fd.level = None
    fd.multiple = False
    f = Files(_record(monkeypatch, registry=[fd]))
    assert await f.checksums() == {}  # file does not exist on disk → omitted


def test_files_working_dirs_classmethod(monkeypatch):
    from clarinet.files.facade import Files

    monkeypatch.setattr(
        "clarinet.files.facade.settings",
        MagicMock(
            storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"
        ),
    )
    monkeypatch.setattr(
        "clarinet.files._storage.settings",
        MagicMock(
            storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"
        ),
    )
    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = Files.working_dirs(
        patient=patient,
        study=None,
        series=None,
        template="{anon_patient_id}/{study_uid}/{series_uid}",
    )
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")


def test_files_misc_classmethods():
    from clarinet.files.facade import Files

    assert Files.validate_template("{patient_id}/{study_uid}/{series_uid}")
    child = MagicMock()
    child.record_type = MagicMock()
    child.record_type.name = "c"
    parent = MagicMock()
    parent.record_type = MagicMock()
    parent.record_type.name = "p"
    assert Files.origin_type(child, parent) == "p"
    assert Files.origin_type(child) == "c"


@pytest.mark.asyncio
async def test_files_in_thread():
    from clarinet.files.facade import Files

    assert await Files.in_thread(lambda x: x + 1, 41) == 42


def test_public_facade_import():
    from clarinet.files import AnonPathError, Files

    assert Files.__name__ == "Files"
    assert issubclass(AnonPathError, Exception)


def test_template_leaf_import_is_light():
    import subprocess
    import sys

    code = (
        "import clarinet.files._template;"
        "import sys;"
        "leaked = [m for m in sys.modules if m == 'clarinet.files.facade'];"
        "assert not leaked, sorted(m for m in sys.modules if m.startswith('clarinet.files'))"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


class TestPathSafety:
    """resolve()/exists()/checksums() are guarded by two independent layers.

    - The value guard, assert_path_safe_value (_template.py), runs during
      rendering on each COERCED substituted value: rejects "/", "\\", NUL
      (_UNSAFE_IN_VALUE), or a value that is exactly "." or "..".
    - join_within (_template.py), a purely lexical containment check on the
      FULLY RENDERED path, runs after rendering completes.

    "/" is in _UNSAFE_IN_VALUE, so a traversal-shaped value like
    "../../etc" trips the value guard's "/" rule before join_within ever
    runs. Tests suffixed "_caught_by_either_layer" use such inputs: with
    the value guard present it raises via "/"; with it removed,
    join_within independently raises on the same rendered path -- neither
    run isolates one layer, and the two raises carry different
    UnsafePathError.value payloads (the bare value vs. the rendered
    filename).

    A bare "." or ".." contains no "/", so it trips the value guard's
    second rule under any pattern -- but only a PREFIXED pattern
    ("seg_{patient_id}.nrrd") isolates it: with the guard removed, a BARE
    pattern renders ".." into "...nrrd", whose leading dot join_within
    rejects on its own, again masking whether the value guard ran. The
    prefix keeps the rendered basename clear of every join_within rule, so
    test_resolve_rejects_bare_dotdot_patient_id and
    test_checksums_rejects_bare_dotdot_patient_id are the tests that pin
    the value guard alone.
    """

    def test_resolve_rejects_absolute_value_caught_by_either_layer(self, monkeypatch):
        # See class docstring: "/etc/passwd" contains "/", so the value
        # guard raises here, not join_within. Also not a regex-legal
        # patient_id (PATIENT_ID_REGEX) -- unreachable through this field
        # in practice.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "{patient_id}.nrrd"
        fd.level = None
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = "/etc/passwd"
        with pytest.raises(UnsafePathError):
            Files(record).resolve("mask")

    def test_resolve_rejects_traversal_value_caught_by_either_layer(self, monkeypatch):
        # See class docstring: "../../etc" contains "/", so the value
        # guard raises here, not join_within. Also not a regex-legal
        # patient_id (PATIENT_ID_REGEX) -- unreachable through this field
        # in practice.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "{patient_id}.nrrd"
        fd.level = None
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = "../../etc"
        with pytest.raises(UnsafePathError):
            Files(record).resolve("mask")

    def test_resolve_rejects_bare_dotdot_patient_id(self, monkeypatch):
        # PATIENT_ID_REGEX (clarinet/models/patient.py) allows any 1-64 chars
        # from A-Za-z0-9._-^, so ".." is a legal patient_id -- this is the
        # scenario the design's plan cites as proof that the temporary
        # {data.*} ban (issue #552) is not sufficient by itself: a
        # regex-legal identity value can still be a bare directory
        # reference, and Files.resolve must still reject it. Prefixed
        # pattern isolates the value guard -- see class docstring.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files
        from clarinet.models.patient import PATIENT_ID_PATTERN

        assert PATIENT_ID_PATTERN.fullmatch("..")  # confirms the premise

        fd = MagicMock()
        fd.name = "seg"
        fd.pattern = "seg_{patient_id}.nrrd"
        fd.level = None
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = ".."
        with pytest.raises(UnsafePathError) as exc_info:
            Files(record).resolve("seg")
        # PHI contract: the raw value travels only on .value, never
        # interpolated into the message (assert_path_safe_value's
        # docstring). Not asserting ".." not in str(exc) here: for this one
        # input, the guard's own message is the fixed phrase "('.' or
        # '..')" describing the rule it enforces, which coincidentally
        # contains the same two characters as the value -- that text is
        # constant and never carries record data, so it is not a PHI leak.
        assert exc_info.value.value == ".."

    def test_exists_inherits_resolve_guards(self, monkeypatch):
        # exists() delegates to resolve(); this pins that it has no
        # separate path. (See class docstring for which layer actually
        # raises on this input.)
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "{patient_id}.nrrd"
        fd.level = None
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = "../../etc"
        with pytest.raises(UnsafePathError):
            Files(record).exists("mask")

    @pytest.mark.asyncio
    async def test_checksums_rejects_escaping_name(self, monkeypatch):
        # The singular branch performs its own working_dir / filename join and
        # is a live bypass if only resolve() is guarded.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "{patient_id}.nrrd"
        fd.level = None
        fd.multiple = False
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = "../../etc"
        with pytest.raises(UnsafePathError):
            await Files(record).checksums()

    @pytest.mark.asyncio
    async def test_checksums_names_the_file_definition(self, monkeypatch):
        # checksums() loops internally, so a caller catching UnsafePathError
        # here has no `fd` of its own in scope (unlike render_for's callers) --
        # record_service.py's _sync_output_files relies on this message to
        # name the file definition in its WARNING (the WARNING is expected to
        # name the record, the file definition, the placeholder key and the
        # reason). The value itself must still travel only on .value, never
        # the message.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "{patient_id}.nrrd"
        fd.level = None
        fd.multiple = False
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = "../../etc"
        with pytest.raises(UnsafePathError) as exc_info:
            await Files(record).checksums()
        assert "mask" in str(exc_info.value)
        assert "../../etc" not in str(exc_info.value)
        assert exc_info.value.value == "../../etc"

    @pytest.mark.asyncio
    async def test_checksums_rejects_bare_dotdot_patient_id(self, monkeypatch):
        # Direct pin for the path_safe=True site in checksums()'s singular
        # branch (facade.py). Prefixed pattern isolates the value guard --
        # see class docstring and test_resolve_rejects_bare_dotdot_patient_id.
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        fd = MagicMock()
        fd.name = "mask"
        fd.pattern = "mask_{patient_id}.nrrd"
        fd.level = None
        fd.multiple = False
        record = _record(monkeypatch, registry=[fd])
        record.patient_id = ".."
        with pytest.raises(UnsafePathError) as exc_info:
            await Files(record).checksums()
        # PHI contract: not asserting ".." not in str(exc) -- the guard's
        # own message is the fixed phrase "('.' or '..')" describing the
        # rule, which would make that assertion false-by-boilerplate (see
        # test_resolve_rejects_bare_dotdot_patient_id).
        assert exc_info.value.value == ".."

    def test_render_is_path_safe(self, monkeypatch):
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        record = _record(monkeypatch)
        record.patient_id = "/etc/passwd"
        with pytest.raises(UnsafePathError):
            Files(record).render("{patient_id}.nrrd")

    def test_render_for_is_path_safe(self, monkeypatch):
        from clarinet.exceptions.domain import UnsafePathError
        from clarinet.files.facade import Files

        record = _record(monkeypatch)
        record.patient_id = "/etc/passwd"
        with pytest.raises(UnsafePathError):
            Files.render_for(record, "{patient_id}.nrrd")

    def test_static_render_template_stays_unguarded(self):
        # Slicer script args may legitimately be absolute paths.
        from clarinet.files.facade import Files

        assert Files.render_template("{p}", {"p": "/opt/slicer/data"}) == "/opt/slicer/data"

    def test_subdirectory_pattern_still_resolves(self, monkeypatch):
        # NEW coverage: no test in the suite exercised a subdirectory pattern
        # before this change, despite the design relying on it staying legal.
        from clarinet.files.facade import Files

        record = _record(monkeypatch)
        record.study_uid = "1.2.840"
        path = Files(record).render("{study_uid}/mask.nrrd")
        assert path == "1.2.840/mask.nrrd"

    def test_multi_dot_basename_still_renders(self, monkeypatch):
        from clarinet.files.facade import Files

        record = _record(monkeypatch)
        assert Files(record).render("mask.seg.nrrd") == "mask.seg.nrrd"
