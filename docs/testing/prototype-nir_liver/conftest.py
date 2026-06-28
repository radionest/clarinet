"""Pytest fixtures for the nir_liver workflow stand test.

The suite drives a *deployed* stand (HTTP API + SSH) end to end, so it is gated
on ``STAND_URL`` and skips entirely when that env var is absent — exactly like
``deploy/test/acceptance`` in the framework. It is therefore safe to collect in
any environment; it only runs when explicitly pointed at a stand.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from workflow_stand import Stand


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "stand: end-to-end test against a deployed stand (needs STAND_URL)"
    )


# Demo studies baked into the golden image (deploy/vm/bake-image.sh fixtures).
DEMO_STUDIES = {
    "DEMO001": {
        "study_uid": "1.2.826.0.1.3680043.10.9999.501087842010466818539916187267295891",
        "patient_name": "Demo Phantom One",
    },
}


def _env(name: str) -> str | None:
    val = os.environ.get(name)
    return val.strip() if val else None


@pytest.fixture(scope="session")
def stand() -> Iterator[Stand]:
    url = _env("STAND_URL")
    if not url:
        pytest.skip("STAND_URL not set — workflow stand test requires a deployed stand")
    password = _env("STAND_ADMIN_PASSWORD")
    ssh_target = _env("STAND_SSH_TARGET")
    ssh_key = _env("STAND_SSH_KEY")
    if not (password and ssh_target and ssh_key):
        pytest.skip("STAND_ADMIN_PASSWORD / STAND_SSH_TARGET / STAND_SSH_KEY required")

    s = Stand(
        base_url=url,
        admin_password=password,
        ssh_target=ssh_target,
        ssh_key=ssh_key,
        known_hosts=_env("STAND_KNOWN_HOSTS"),
        pacs_http=_env("STAND_PACS_HTTP") or "http://localhost:8042",
    )
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def demo_patient() -> tuple[str, dict]:
    """The demo patient/study this run drives (DEMO001 — 2 CT series)."""
    pid = "DEMO001"
    return pid, DEMO_STUDIES[pid]
