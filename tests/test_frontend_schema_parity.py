"""FileDefinitionRead ↔ Gleam frontend parity.

The record-type edit page PATCHes the whole file_registry back to the server,
and the backend treats it as authoritative — any FileDefinitionRead field the
Gleam model or its serializer misses is silently nulled on the global
FileDefinition row by the next UI save (PR #524 review, finding C1). This test
turns that class of bug into a red test instead of a data-loss incident.
"""

import re
from pathlib import Path

from clarinet.models.file_schema import FileDefinitionRead

_REPO = Path(__file__).resolve().parents[1]
_MODELS_GLEAM = _REPO / "clarinet" / "frontend" / "src" / "api" / "models.gleam"
_RECORDS_GLEAM = _REPO / "clarinet" / "frontend" / "src" / "api" / "records.gleam"
_EDIT_GLEAM = _REPO / "clarinet" / "frontend" / "src" / "pages" / "record_types" / "edit.gleam"


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    return source[start : source.index(end_marker, start)]


def test_gleam_type_carries_every_read_field():
    block = _block(_MODELS_GLEAM.read_text(), "pub type FileDefinition {", "}")
    missing = [f for f in FileDefinitionRead.model_fields if not re.search(rf"\b{f}:", block)]
    assert not missing, f"models.gleam FileDefinition lacks fields: {missing}"


def test_gleam_decoder_reads_every_read_field():
    block = _block(_RECORDS_GLEAM.read_text(), "fn file_definition_decoder", "\n}")
    missing = [f for f in FileDefinitionRead.model_fields if f'"{f}"' not in block]
    assert not missing, f"file_definition_decoder skips fields: {missing}"


def test_edit_serializer_writes_every_read_field():
    block = _block(_EDIT_GLEAM.read_text(), "fn file_definitions_to_json", "\n}")
    missing = [f for f in FileDefinitionRead.model_fields if f'#("{f}"' not in block]
    assert not missing, f"file_definitions_to_json drops fields: {missing}"
