"""The pipeline tests' RabbitMQ gate: skip when no broker listens, unless one is required."""

import socket
from collections.abc import Iterator

import pytest

from tests.utils import rabbitmq


@pytest.fixture
def closed_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def open_port() -> Iterator[int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


def _point_at(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    monkeypatch.setattr(rabbitmq, "RABBITMQ_HOST", "127.0.0.1")
    monkeypatch.setattr(rabbitmq, "RABBITMQ_PORT", port)


def test_absent_broker_skips(monkeypatch: pytest.MonkeyPatch, closed_port: int) -> None:
    _point_at(monkeypatch, closed_port)
    monkeypatch.delenv("CLARINET_TEST_REQUIRE_RABBITMQ", raising=False)
    with pytest.raises(pytest.skip.Exception, match="not reachable"):
        rabbitmq.skip_unless_rabbitmq_reachable()


def test_absent_required_broker_fails(monkeypatch: pytest.MonkeyPatch, closed_port: int) -> None:
    """test-all-stages sets the switch: a skip there would finish the stage green."""
    _point_at(monkeypatch, closed_port)
    monkeypatch.setenv("CLARINET_TEST_REQUIRE_RABBITMQ", "1")
    with pytest.raises(pytest.fail.Exception, match="CLARINET_TEST_REQUIRE_RABBITMQ"):
        rabbitmq.skip_unless_rabbitmq_reachable()


def test_listening_broker_passes(monkeypatch: pytest.MonkeyPatch, open_port: int) -> None:
    _point_at(monkeypatch, open_port)
    monkeypatch.setenv("CLARINET_TEST_REQUIRE_RABBITMQ", "1")
    rabbitmq.skip_unless_rabbitmq_reachable()
