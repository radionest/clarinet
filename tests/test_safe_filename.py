"""Unit tests for clarinet.api.routers.reports._safe_filename."""

from clarinet.api.routers.reports import _safe_filename


def test_strips_double_quote() -> None:
    assert '"' not in _safe_filename('evil"name', "csv")


def test_strips_newline() -> None:
    assert "\n" not in _safe_filename("evil\nname", "csv")
    assert "\r" not in _safe_filename("evil\rname", "csv")


def test_strips_slash() -> None:
    assert "/" not in _safe_filename("evil/name", "csv")


def test_preserves_clean_name() -> None:
    result = _safe_filename("clean_name", "csv")
    assert result.startswith("clean_name_")
    assert result.endswith(".csv")


def test_preserves_dot_and_hyphen() -> None:
    # Stems can legitimately contain dots and hyphens — keep them intact.
    result = _safe_filename("v1.2-final", "xlsx")
    assert "v1.2-final" in result
    assert result.endswith(".xlsx")
