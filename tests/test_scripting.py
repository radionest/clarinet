"""Unit tests for clarinet.scripting — the @script frame for operational scripts."""

from __future__ import annotations

import io
import sys
from unittest import mock

import pytest
import typer
from typer.testing import CliRunner

from clarinet.scripting import ScriptCtx, Tally, script
from clarinet.settings import settings

runner = CliRunner()


def test_tally_counts_and_getitem() -> None:
    tally = Tally()
    tally.count("checked")
    tally.count("checked")
    tally.count("created", 3)
    assert tally["checked"] == 2
    assert tally["created"] == 3
    assert tally["missing"] == 0


def test_tally_fail_records_and_summary() -> None:
    tally = Tally()
    tally.count("checked", 2)
    tally.fail("record 7", "boom")
    lines = tally.summary_lines()
    assert "checked: 2" in lines
    assert "failed: 1" in lines
    assert any("record 7" in line and "boom" in line for line in lines)


def test_tally_empty_summary() -> None:
    assert Tally().summary_lines() == ["failed: 0"]


def test_ctx_defaults() -> None:
    ctx = ScriptCtx()
    assert ctx.commit is False
    assert ctx.limit is None
    assert ctx.yes is False
    assert ctx.api_base is None
    assert isinstance(ctx.tally, Tally)


def test_hit_limit_unset() -> None:
    ctx = ScriptCtx()
    assert not ctx.hit_limit(0)
    assert not ctx.hit_limit(10_000)


def test_hit_limit_boundary() -> None:
    ctx = ScriptCtx(limit=2)
    assert not ctx.hit_limit(1)
    assert ctx.hit_limit(2)
    assert ctx.hit_limit(3)


def test_would_line(capsys) -> None:
    ScriptCtx().would("invalidate record 7")
    assert capsys.readouterr().out == "[dry-run] would invalidate record 7\n"


def test_confirm_yes_flag_skips_prompt(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))
    assert ScriptCtx(yes=True).confirm("proceed?") is True


def test_confirm_non_tty_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # isatty() -> False
    assert ScriptCtx().confirm("proceed?") is False
    assert "--yes" in capsys.readouterr().out


def test_confirm_tty_prompts(monkeypatch) -> None:
    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = True
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert ScriptCtx().confirm("proceed?") is True

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert ScriptCtx().confirm("proceed?") is False


class _FakeClient:
    def __init__(self, base_url: str, **kwargs: object) -> None:
        self.base_url = base_url
        self.kwargs = kwargs


def test_client_lazy_cached_and_settings_based(monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_base_url", "http://from-settings/api")
    with mock.patch("clarinet.client.ClarinetClient", _FakeClient):
        ctx = ScriptCtx()
        first = ctx.client
        assert isinstance(first, _FakeClient)
        assert first.base_url == "http://from-settings/api"
        assert ctx.client is first  # cached


def test_client_api_base_override() -> None:
    with mock.patch("clarinet.client.ClarinetClient", _FakeClient):
        ctx = ScriptCtx(api_base="http://override:9999")
        assert ctx.client.base_url == "http://override:9999"


def test_client_passes_token_and_ssl(monkeypatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "api_base_url", "http://x/api")
    monkeypatch.setattr(settings, "api_verify_ssl", False)
    monkeypatch.setattr(settings, "internal_service_token", SecretStr("tok-123"))
    with mock.patch("clarinet.client.ClarinetClient", _FakeClient):
        ctx = ScriptCtx()
        assert ctx.client.kwargs == {"service_token": "tok-123", "verify_ssl": False}


def test_no_client_when_untouched() -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ClarinetClient must not be constructed")

    with mock.patch("clarinet.client.ClarinetClient", _boom):
        ctx = ScriptCtx()
        ctx.tally.count("checked")
        assert ctx.hit_limit(5) is False
        # reaching here without AssertionError = no construction happened


def test_custom_option_passthrough() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx, series: str | None = None) -> None:
        """Sample framed script."""
        seen["commit"] = ctx.commit
        seen["series"] = series

    result = runner.invoke(main.app, ["--series", "1.2.3"])
    assert result.exit_code == 0
    assert seen == {"commit": False, "series": "1.2.3"}


def test_standard_options_reach_ctx() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""
        seen.update(commit=ctx.commit, limit=ctx.limit, yes=ctx.yes, api_base=ctx.api_base)

    result = runner.invoke(
        main.app, ["--commit", "--limit", "5", "--yes", "--api-base", "http://x"]
    )
    assert result.exit_code == 0
    assert seen == {"commit": True, "limit": 5, "yes": True, "api_base": "http://x"}


def test_positional_argument_supported() -> None:
    seen: dict[str, object] = {}

    @script()
    async def main(ctx: ScriptCtx, xlsx: str) -> None:
        """With a required positional argument."""
        seen["xlsx"] = xlsx

    result = runner.invoke(main.app, ["cohort.xlsx"])
    assert result.exit_code == 0
    assert seen["xlsx"] == "cohort.xlsx"


def test_collision_raises_at_decoration_time() -> None:
    with pytest.raises(TypeError, match="commit"):

        @script()
        async def main(ctx: ScriptCtx, commit: bool = False) -> None:
            """Colliding parameter."""


def test_first_param_must_be_ctx() -> None:
    with pytest.raises(TypeError, match="ctx"):

        @script()
        async def main(series: str) -> None:
            """Missing ctx."""


def test_var_kwargs_rejected() -> None:
    with pytest.raises(TypeError, match="kwargs"):

        @script()
        async def main(ctx: ScriptCtx, **kwargs: str) -> None:
            """Var kwargs unsupported."""


def test_entry_is_callable_and_exposes_app() -> None:
    @script()
    async def main(ctx: ScriptCtx) -> None:
        """Sample."""

    assert callable(main)
    assert isinstance(main.app, typer.Typer)
