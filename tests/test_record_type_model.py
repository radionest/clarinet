import pytest
from pydantic import ValidationError

from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.record_type import RecordType, RecordTypeCreate
from clarinet.utils.logger import logger


def test_unique_by_default_and_canonical():
    assert RecordTypeCreate(name="x", unique_by=["user", "parent"]).unique_by == frozenset(
        {"parent", "user"}
    )
    assert RecordTypeCreate(name="y").unique_by == frozenset({"user", "parent"})
    assert RecordTypeCreate(name="z", unique_by=None).unique_by is None


def test_empty_unique_by_rejected():
    with pytest.raises(ValidationError, match="max_records=1"):
        RecordTypeCreate(name="e", unique_by=set())


def test_shared_editing_requires_no_user_partition():
    with pytest.raises(ValidationError, match="shared_editing"):
        RecordTypeCreate(name="s", shared_editing=True, unique_by={"user"})


def test_legacy_key_translated_on_create():
    with pytest.warns(DeprecationWarning):
        rt = RecordTypeCreate(name="l", unique_per_user=False)
    assert rt.unique_by is None  # NOT silently ignored into the default


def test_legacy_key_translated_on_patch():
    from clarinet.models.record_type import RecordTypeOptional

    with pytest.warns(DeprecationWarning):
        p = RecordTypeOptional(unique_per_user=True)
    assert p.unique_by == frozenset({"user"}) and "unique_by" in p.model_fields_set


def test_optional_unique_by_canonicalized_at_dto_layer():
    from clarinet.models.record_type import RecordTypeOptional

    with pytest.raises(ValidationError, match="max_records=1"):
        RecordTypeOptional(unique_by=set())
    with pytest.raises(ValidationError, match="series"):
        RecordTypeOptional(unique_by={"series"})
    with pytest.raises(ValidationError, match="USER"):
        RecordTypeOptional(unique_by=["USER"])
    assert RecordTypeOptional(unique_by=["user", "user"]).unique_by == frozenset({"user"})
    assert RecordTypeOptional(unique_by=False).unique_by is None
    assert RecordTypeOptional(unique_by=None).unique_by is None


def test_non_iterable_unique_by_is_validation_error_not_500():
    from clarinet.models.record_type import RecordTypeOptional

    # A TypeError escaping the before-validator would surface as HTTP 500;
    # both DTOs must turn garbage scalars into a plain ValidationError (422).
    with pytest.raises(ValidationError, match="got int"):
        RecordTypeCreate(name="x", unique_by=7)
    with pytest.raises(ValidationError, match="got int"):
        RecordTypeOptional(unique_by=7)


# --- RecordType.file_registry (ORM property) must not blank out the whole
# registry when one stored FileDefinition.pattern predates the path-safety
# validator on FileDefinitionRead (see file_schema.py; FileDefinition itself
# is table=True, so SQLModel skips validation there — that is exactly what
# lets a hostile row exist / be constructed at all, and is already pinned by
# tests/test_path_safety.py::test_table_model_is_intentionally_unvalidated).
# One bad definition must be skipped + logged, not swallow its siblings. ---


@pytest.fixture(autouse=True)
def _reset_skipped_definition_warnings():
    """Clear the process-wide warn-once set so these cases stay order-independent.

    ``RecordType.file_registry`` reports each bad definition once per process;
    without this every test after the first would observe zero warnings.
    """
    from clarinet.models.record_type import _warned_skipped_definitions

    _warned_skipped_definitions.clear()
    yield
    _warned_skipped_definitions.clear()


@pytest.fixture
def captured_records():
    """Capture every loguru record emitted during the test as raw dicts.

    Mirrors tests/test_auth_logging.py's fixture of the same name/shape.
    """
    records: list[dict] = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


def _record_type_with_one_legacy_and_one_valid_file() -> RecordType:
    rt = RecordType(name="legacy-mix", level="SERIES")
    rt.file_links = [
        RecordTypeFileLink(
            record_type_name="legacy-mix",
            file_definition=FileDefinition(name="good_file", pattern="report.pdf"),
            role=FileRole.OUTPUT,
            required=True,
        ),
        RecordTypeFileLink(
            record_type_name="legacy-mix",
            file_definition=FileDefinition(
                name="legacy_file", pattern="birads_{data.BIRADS_R}.txt"
            ),
            role=FileRole.OUTPUT,
            required=True,
        ),
    ]
    return rt


def test_file_registry_skips_legacy_pattern_keeps_valid_sibling():
    rt = _record_type_with_one_legacy_and_one_valid_file()

    registry = rt.file_registry

    assert registry is not None
    assert [fd.name for fd in registry] == ["good_file"]


def test_file_registry_warns_naming_the_offending_definition(captured_records):
    rt = _record_type_with_one_legacy_and_one_valid_file()

    _ = rt.file_registry

    warnings = [r for r in captured_records if r["level"].name == "WARNING"]
    assert len(warnings) == 1
    message = warnings[0]["message"]
    assert "legacy-mix" in message  # record type name
    assert "legacy_file" in message  # file definition name


def test_file_registry_warns_once_per_definition_not_once_per_read(captured_records):
    """A legacy row must not log a WARNING on every request for ever.

    ``file_registry`` is a property, re-evaluated on every record-type read, so
    a deployment holding one un-migrated ``{data.*}`` pattern emitted a WARNING
    per request — unbounded log volume for a fact that never changes.
    """
    rt = _record_type_with_one_legacy_and_one_valid_file()

    for _ in range(3):
        _ = rt.file_registry

    warnings = [r for r in captured_records if r["level"].name == "WARNING"]
    assert len(warnings) == 1


def test_file_registry_still_skips_the_bad_definition_after_the_warning_is_deduped():
    """Dedup must silence the log line, never the skip itself."""
    rt = _record_type_with_one_legacy_and_one_valid_file()

    first = rt.file_registry
    second = rt.file_registry

    assert [fd.name for fd in first] == ["good_file"]
    assert [fd.name for fd in second] == ["good_file"]
