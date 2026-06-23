"""Unit tests for the cross-file ``$defs`` bundler."""

import json
from pathlib import Path

import pytest

from clarinet.exceptions.domain import ConfigLoadError
from clarinet.utils.schema_bundler import bundle_external_defs


def _write(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_named_ref_is_hoisted_and_rewritten(tmp_path: Path) -> None:
    _write(
        tmp_path / "common.schema.json",
        {"$defs": {"Lesion": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
    )
    schema = {
        "type": "object",
        "properties": {"lesion": {"$ref": "common.schema.json#/$defs/Lesion"}},
    }
    out = bundle_external_defs(schema, tmp_path)
    assert out["properties"]["lesion"] == {"$ref": "#/$defs/Lesion"}
    assert out["$defs"]["Lesion"]["properties"]["n"] == {"type": "integer"}


def test_input_not_mutated(tmp_path: Path) -> None:
    _write(tmp_path / "common.schema.json", {"$defs": {"X": {"type": "string"}}})
    schema = {"properties": {"a": {"$ref": "common.schema.json#/$defs/X"}}}
    before = json.dumps(schema)
    bundle_external_defs(schema, tmp_path)
    assert json.dumps(schema) == before


def test_intra_document_ref_untouched(tmp_path: Path) -> None:
    schema = {
        "$defs": {"X": {"type": "string"}},
        "properties": {"a": {"$ref": "#/$defs/X"}},
    }
    out = bundle_external_defs(schema, tmp_path)
    assert out["properties"]["a"] == {"$ref": "#/$defs/X"}


def test_sibling_ref_inside_def_is_pulled(tmp_path: Path) -> None:
    _write(
        tmp_path / "common.schema.json",
        {
            "$defs": {
                "Outer": {"type": "object", "properties": {"inner": {"$ref": "#/$defs/Inner"}}},
                "Inner": {"type": "string", "enum": ["a", "b"]},
            }
        },
    )
    schema = {"properties": {"o": {"$ref": "common.schema.json#/$defs/Outer"}}}
    out = bundle_external_defs(schema, tmp_path)
    assert out["$defs"]["Outer"]["properties"]["inner"] == {"$ref": "#/$defs/Inner"}
    assert out["$defs"]["Inner"]["enum"] == ["a", "b"]


def test_deep_walk_reaches_refs_under_items_and_allof(tmp_path: Path) -> None:
    _write(tmp_path / "c.schema.json", {"$defs": {"Item": {"type": "integer"}}})
    schema = {
        "allOf": [
            {
                "properties": {
                    "arr": {"type": "array", "items": {"$ref": "c.schema.json#/$defs/Item"}}
                }
            }
        ]
    }
    out = bundle_external_defs(schema, tmp_path)
    assert out["allOf"][0]["properties"]["arr"]["items"] == {"$ref": "#/$defs/Item"}
    assert out["$defs"]["Item"] == {"type": "integer"}


def test_missing_file_raises(tmp_path: Path) -> None:
    schema = {"properties": {"a": {"$ref": "nope.schema.json#/$defs/X"}}}
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)


def test_missing_pointer_raises(tmp_path: Path) -> None:
    _write(tmp_path / "common.schema.json", {"$defs": {"X": {"type": "string"}}})
    schema = {"properties": {"a": {"$ref": "common.schema.json#/$defs/Missing"}}}
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)


def test_whole_file_ref_raises(tmp_path: Path) -> None:
    _write(tmp_path / "common.schema.json", {"type": "string"})
    schema = {"properties": {"a": {"$ref": "common.schema.json"}}}
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)


def test_cross_file_chain_raises(tmp_path: Path) -> None:
    _write(tmp_path / "a.schema.json", {"$defs": {"A": {"$ref": "b.schema.json#/$defs/B"}}})
    _write(tmp_path / "b.schema.json", {"$defs": {"B": {"type": "string"}}})
    schema = {"properties": {"a": {"$ref": "a.schema.json#/$defs/A"}}}
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)


def test_name_collision_raises(tmp_path: Path) -> None:
    _write(tmp_path / "common.schema.json", {"$defs": {"X": {"type": "string"}}})
    schema = {
        "$defs": {"X": {"type": "integer"}},  # same key, different content
        "properties": {"a": {"$ref": "common.schema.json#/$defs/X"}},
    }
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)


def test_idempotent_on_already_bundled_schema(tmp_path: Path) -> None:
    _write(tmp_path / "common.schema.json", {"$defs": {"X": {"type": "string"}}})
    schema = {"properties": {"a": {"$ref": "common.schema.json#/$defs/X"}}}
    once = bundle_external_defs(schema, tmp_path)
    twice = bundle_external_defs(once, tmp_path)
    assert twice == once


def test_definitions_pointer_is_supported(tmp_path: Path) -> None:
    _write(
        tmp_path / "legacy.schema.json", {"definitions": {"X": {"type": "string", "enum": ["a"]}}}
    )
    schema = {"properties": {"a": {"$ref": "legacy.schema.json#/definitions/X"}}}
    out = bundle_external_defs(schema, tmp_path)
    assert out["properties"]["a"] == {"$ref": "#/$defs/X"}
    assert out["$defs"]["X"]["enum"] == ["a"]


def test_same_name_from_two_files_raises(tmp_path: Path) -> None:
    _write(tmp_path / "a.schema.json", {"$defs": {"Dup": {"type": "string"}}})
    _write(tmp_path / "b.schema.json", {"$defs": {"Dup": {"type": "integer"}}})
    schema = {
        "properties": {
            "a": {"$ref": "a.schema.json#/$defs/Dup"},
            "b": {"$ref": "b.schema.json#/$defs/Dup"},
        }
    }
    with pytest.raises(ConfigLoadError):
        bundle_external_defs(schema, tmp_path)
