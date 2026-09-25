"""Regression test for #548: `with_context` must not mutate the shared singletons."""

from clarinet.exceptions.http import CONFLICT, UNAUTHORIZED


def test_with_context_returns_copy_and_leaves_singleton_intact() -> None:
    default = CONFLICT.detail

    a = CONFLICT.with_context("record already finished")
    b = CONFLICT.with_context("other conflict")

    assert a is not CONFLICT
    assert (a.detail, b.detail) == ("record already finished", "other conflict")
    assert CONFLICT.detail == default
    assert a.status_code == CONFLICT.status_code
    assert UNAUTHORIZED.with_context("x").headers == UNAUTHORIZED.headers
