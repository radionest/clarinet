"""Tests for the unified type-aware path template renderer.

The unified renderer (``clarinet.utils.path_template.render_template``) is the
single source of truth for ``{placeholder}`` interpolation across the codebase:

- ``_safe_render`` (storage_paths.py) — directory segments, STRICT mode.
- ``resolve_pattern_from_dict`` (file_resolver.py) — file patterns, LENIENT mode.
- ``_resolve_custom_args`` (slicer/context.py) — Slicer args, STRICT + warn.

These tests exercise the primitive directly; the three shim regression tests
verify back-compat with the historical call sites.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from clarinet.services.common.file_resolver import resolve_pattern_from_dict
from clarinet.services.common.storage_paths import _safe_render
from clarinet.services.slicer.context import _resolve_custom_args
from clarinet.utils.path_template import (
    RenderMode,
    coerce_field_value,
    render_template,
)

# ── coerce_field_value ────────────────────────────────────────────────────────


class TestCoerceFieldValue:
    def test_str_pass_through(self):
        assert coerce_field_value("abc") == "abc"

    def test_empty_str_pass_through(self):
        assert coerce_field_value("") == ""

    def test_int(self):
        assert coerce_field_value(42) == "42"

    def test_float(self):
        assert coerce_field_value(3.5) == "3.5"

    def test_bool_true_lowercase(self):
        assert coerce_field_value(True) == "true"

    def test_bool_false_lowercase(self):
        assert coerce_field_value(False) == "false"

    def test_none_returns_none(self):
        assert coerce_field_value(None) is None

    def test_list_joins_sorted(self):
        assert coerce_field_value(["SR", "CT"]) == "CT_SR"

    def test_tuple_joins_sorted(self):
        assert coerce_field_value(("SR", "CT")) == "CT_SR"

    def test_set_joins_sorted(self):
        assert coerce_field_value({"SR", "CT"}) == "CT_SR"

    def test_frozenset_joins_sorted(self):
        assert coerce_field_value(frozenset({"SR", "CT"})) == "CT_SR"

    def test_list_with_nones_filtered(self):
        assert coerce_field_value(["CT", None, ""]) == "CT"

    def test_empty_list_returns_none(self):
        assert coerce_field_value([]) is None

    def test_dict_raises_value_error(self):
        with pytest.raises(ValueError, match="cannot interpolate dict"):
            coerce_field_value({"x": 1})

    def test_custom_separator(self):
        assert coerce_field_value(["b", "a"], list_separator="-") == "a-b"

    def test_unsorted_keeps_order(self):
        assert coerce_field_value(["SR", "CT"], list_sorted=False) == "SR_CT"

    def test_path_object(self):
        assert coerce_field_value(Path("/tmp/x")) == str(Path("/tmp/x"))

    def test_uuid(self):
        u = UUID("12345678-1234-5678-1234-567812345678")
        assert coerce_field_value(u) == str(u)


# ── render_template — LENIENT mode ────────────────────────────────────────────


class TestRenderTemplateLenient:
    def test_simple_substitution(self):
        assert render_template("file_{id}.txt", {"id": 42}) == "file_42.txt"

    def test_dotted_path(self):
        assert (
            render_template("birads_{data.BIRADS_R}.txt", {"data": {"BIRADS_R": 4}})
            == "birads_4.txt"
        )

    def test_dotted_path_missing_returns_empty(self):
        assert render_template("file_{data.missing}.txt", {"data": {"x": 1}}) == "file_.txt"

    def test_dotted_path_on_non_dict_returns_empty(self):
        assert render_template("file_{a.b}.txt", {"a": "scalar"}) == "file_.txt"

    def test_missing_key_default_empty(self):
        assert render_template("file_{missing}.txt", {"id": 1}) == "file_.txt"

    def test_missing_key_leave_as_is(self):
        assert (
            render_template("file_{missing}.txt", {"id": 1}, on_missing_leave_as_is=True)
            == "file_{missing}.txt"
        )

    def test_list_value_joined_sorted(self):
        # The user-visible fix: list value must NOT render as Python repr.
        assert render_template("modalities_{m}.txt", {"m": ["SR", "CT"]}) == "modalities_CT_SR.txt"

    def test_list_in_nested_dict_joined(self):
        # `record.data` may carry list-valued fields; file patterns now join them.
        assert (
            render_template(
                "file_{data.modalities}.nrrd",
                {"data": {"modalities": ["SR", "CT"]}},
            )
            == "file_CT_SR.nrrd"
        )

    def test_dict_value_substitutes_missing(self):
        assert render_template("x_{d}.txt", {"d": {"nested": "x"}}) == "x_.txt"

    def test_no_placeholders(self):
        assert render_template("static.nrrd", {"id": 1}) == "static.nrrd"

    def test_none_value_substitutes_missing(self):
        assert render_template("x_{v}.txt", {"v": None}) == "x_.txt"

    def test_empty_list_substitutes_missing(self):
        assert render_template("x_{m}.txt", {"m": []}) == "x_.txt"


# ── render_template — STRICT mode ─────────────────────────────────────────────


class TestRenderTemplateStrict:
    def test_strict_raises_keyerror_on_missing(self):
        with pytest.raises(KeyError) as exc:
            render_template("file_{missing}.txt", {"id": 1}, mode=RenderMode.STRICT)
        assert exc.value.args[0] == "missing"

    def test_strict_raises_keyerror_on_none(self):
        with pytest.raises(KeyError) as exc:
            render_template("x_{v}.txt", {"v": None}, mode=RenderMode.STRICT)
        assert exc.value.args[0] == "v"

    def test_strict_raises_keyerror_on_empty_list(self):
        with pytest.raises(KeyError):
            render_template("x_{m}.txt", {"m": []}, mode=RenderMode.STRICT)

    def test_strict_raises_valueerror_on_dict(self):
        with pytest.raises(ValueError, match="cannot interpolate dict"):
            render_template("x_{d}.txt", {"d": {"k": "v"}}, mode=RenderMode.STRICT)

    def test_strict_coerces_list_same_as_lenient(self):
        assert (
            render_template("m_{m}.txt", {"m": ["SR", "CT"]}, mode=RenderMode.STRICT)
            == "m_CT_SR.txt"
        )

    def test_strict_empty_string_passes_through(self):
        # Caller (e.g. _safe_render) decides whether "" is unsafe.
        assert render_template("x_{v}.txt", {"v": ""}, mode=RenderMode.STRICT) == "x_.txt"

    def test_strict_simple_substitution(self):
        assert render_template("{a}/{b}", {"a": "x", "b": "y"}, mode=RenderMode.STRICT) == "x/y"


# ── Back-compat shims ─────────────────────────────────────────────────────────


class TestBackCompatShims:
    """Verify the three legacy helpers still behave the same after delegation."""

    # ── _safe_render ──

    def test_safe_render_basic(self):
        assert _safe_render("{patient_id}", {"patient_id": "P1"}) == "P1"

    def test_safe_render_missing_raises_anon_path_error(self):
        from clarinet.exceptions.domain import AnonPathError

        with pytest.raises(AnonPathError, match="unknown placeholder"):
            _safe_render("{missing}", {"patient_id": "P1"})

    def test_safe_render_slash_rejected(self):
        from clarinet.exceptions.domain import AnonPathError

        with pytest.raises(AnonPathError, match="unsafe rendered segment"):
            _safe_render("{x}", {"x": "a/b"})

    def test_safe_render_dotdot_rejected(self):
        from clarinet.exceptions.domain import AnonPathError

        with pytest.raises(AnonPathError, match="unsafe rendered segment"):
            _safe_render("{x}", {"x": ".."})

    def test_safe_render_hidden_rejected(self):
        from clarinet.exceptions.domain import AnonPathError

        with pytest.raises(AnonPathError, match="unsafe rendered segment"):
            _safe_render("{x}", {"x": ".hidden"})

    def test_safe_render_empty_rejected(self):
        from clarinet.exceptions.domain import AnonPathError

        with pytest.raises(AnonPathError, match="unsafe rendered segment"):
            _safe_render("{x}", {"x": ""})

    # ── resolve_pattern_from_dict ──

    def test_resolve_pattern_simple(self):
        assert resolve_pattern_from_dict("file_{id}.txt", {"id": 42}) == "file_42.txt"

    def test_resolve_pattern_dotted(self):
        assert (
            resolve_pattern_from_dict("birads_{data.BIRADS_R}.txt", {"data": {"BIRADS_R": 4}})
            == "birads_4.txt"
        )

    def test_resolve_pattern_missing_returns_empty(self):
        assert resolve_pattern_from_dict("file_{missing}.txt", {"id": 1}) == "file_.txt"

    def test_resolve_pattern_no_placeholders(self):
        assert resolve_pattern_from_dict("static.nrrd", {"id": 1}) == "static.nrrd"

    def test_resolve_pattern_list_value_joined_sorted(self):
        # The user-visible fix at the file-pattern layer.
        assert (
            resolve_pattern_from_dict(
                "file_{data.modalities}.nrrd",
                {"data": {"modalities": ["SR", "CT"]}},
            )
            == "file_CT_SR.nrrd"
        )

    # ── _resolve_custom_args ──

    def test_custom_args_resolves(self):
        result = _resolve_custom_args({"output": "{study_uid}.nii"}, {"study_uid": "1.2.3"})
        assert result == {"output": "1.2.3.nii"}

    def test_custom_args_unresolvable_skipped(self):
        # Missing placeholder logged and key skipped from output.
        result = _resolve_custom_args(
            {"good": "{study_uid}", "bad": "{nonexistent}"},
            {"study_uid": "1.2.3"},
        )
        assert result == {"good": "1.2.3"}
        assert "bad" not in result

    def test_custom_args_empty_dict(self):
        assert _resolve_custom_args(None, {"x": "y"}) == {}
        assert _resolve_custom_args({}, {"x": "y"}) == {}

    def test_custom_args_list_value_joined(self):
        # Slicer args also benefit from list coercion if a user-defined
        # template references a list-valued context variable.
        result = _resolve_custom_args({"mods": "{modalities}"}, {"modalities": ["SR", "CT"]})
        assert result == {"mods": "CT_SR"}
