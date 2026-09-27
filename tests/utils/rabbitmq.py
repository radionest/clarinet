"""Shared RabbitMQ gate for the pipeline tests against a live broker."""

import os
import socket

import pytest

from tests.config import RABBITMQ_HOST, RABBITMQ_PORT


def skip_unless_rabbitmq_reachable() -> None:
    """Skip when no broker listens; fail instead under ``CLARINET_TEST_REQUIRE_RABBITMQ=1``.

    ``make test-all-stages`` sets the switch wherever it points the tests at its
    own VM broker (``scripts/vm-test-env.sh``): an unreachable broker there is a
    broken stage, and a skip would let it finish green without the pipeline tests.
    The failure carries no traceback: it repeats for every pipeline test, and the
    chained socket error adds nothing but the stdlib source of ``create_connection``.
    """
    try:
        socket.create_connection((RABBITMQ_HOST, RABBITMQ_PORT), timeout=3).close()
    except OSError as e:
        reason = f"RabbitMQ not reachable at {RABBITMQ_HOST}:{RABBITMQ_PORT}"
        if os.environ.get("CLARINET_TEST_REQUIRE_RABBITMQ") == "1":
            pytest.fail(f"{reason} ({e}) but CLARINET_TEST_REQUIRE_RABBITMQ=1", pytrace=False)
        pytest.skip(reason)
