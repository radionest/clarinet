"""Single source of truth for test service addresses.

All hosts default to ``localhost`` so tests work out of the box against a
local Docker stack (``docker-compose.test.yml``). Override via env vars
(``CLARINET_TEST_*``) when running against a remote VM such as klara::

    export CLARINET_TEST_RABBITMQ_HOST=192.168.122.151
    export CLARINET_TEST_PACS_HOST=192.168.122.151
    export CLARINET_TEST_PG_HOST=192.168.122.151

A ``.env.test`` file at the repo root is loaded automatically if present
(see ``.env.test.example``). Env vars take precedence over the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env_test() -> None:
    """Load .env.test into os.environ without overriding existing values."""
    env_file = Path(__file__).resolve().parent.parent / ".env.test"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_test()

# ─── RabbitMQ ────────────────────────────────────────────────────────────────

RABBITMQ_HOST = os.environ.get("CLARINET_TEST_RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("CLARINET_TEST_RABBITMQ_PORT", "5672"))
RABBITMQ_MANAGEMENT_PORT = int(os.environ.get("CLARINET_TEST_RABBITMQ_MANAGEMENT_PORT", "15672"))
RABBITMQ_USER = os.environ.get("CLARINET_TEST_RABBITMQ_USER", "clarinet_test")
RABBITMQ_PASS = os.environ.get("CLARINET_TEST_RABBITMQ_PASS", "clarinet_test")
RABBITMQ_MANAGEMENT_AUTH: tuple[str, str] = (
    os.environ.get("CLARINET_TEST_RABBITMQ_MANAGEMENT_USER", RABBITMQ_USER),
    os.environ.get("CLARINET_TEST_RABBITMQ_MANAGEMENT_PASS", RABBITMQ_PASS),
)
RABBITMQ_URL = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
RABBITMQ_MANAGEMENT_URL = f"http://{RABBITMQ_HOST}:{RABBITMQ_MANAGEMENT_PORT}"

# ─── Orthanc PACS ────────────────────────────────────────────────────────────

PACS_HOST = os.environ.get("CLARINET_TEST_PACS_HOST", "localhost")
PACS_PORT = int(os.environ.get("CLARINET_TEST_PACS_PORT", "4242"))
PACS_REST_PORT = int(os.environ.get("CLARINET_TEST_PACS_REST_PORT", "8042"))
PACS_REST_URL = f"http://{PACS_HOST}:{PACS_REST_PORT}"
PACS_AET = os.environ.get("CLARINET_TEST_PACS_AET", "ORTHANC")
CALLING_AET = os.environ.get("CLARINET_TEST_CALLING_AET", "CLARINET_TEST")

# ─── 3D Slicer ───────────────────────────────────────────────────────────────

SLICER_HOST = os.environ.get("CLARINET_TEST_SLICER_HOST", "localhost")
SLICER_PORT = int(os.environ.get("CLARINET_TEST_SLICER_PORT", "2016"))

# ─── PostgreSQL (migration / per-worker test DBs) ────────────────────────────

PG_HOST = os.environ.get("CLARINET_TEST_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("CLARINET_TEST_PG_PORT", "5432"))
PG_USER = os.environ.get("CLARINET_TEST_PG_USER", "clarinet_test")
PG_PASS = os.environ.get("CLARINET_TEST_PG_PASS", "clarinet_test")
PG_MIGRATION_DB = os.environ.get("CLARINET_TEST_PG_MIGRATION_DB", "clarinet_mig_base")


def pg_url(database: str = PG_MIGRATION_DB) -> str:
    """Build an asyncpg URL for a given test database name."""
    return f"postgresql+asyncpg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{database}"
