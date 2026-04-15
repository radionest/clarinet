"""RabbitMQ test resource cleanup via Management HTTP API.

Provides functions to list and delete orphaned test queues/exchanges
left behind by crashed or interrupted test runs.
"""

from urllib.parse import quote

import httpx

from clarinet.utils.logger import logger

# Patterns identifying test resources (queues)
TEST_QUEUE_PREFIXES = ("test_", "e2e_", "clarinet_startup_test.", "taskiq.")

# Patterns identifying test resources (exchanges)
TEST_EXCHANGE_PREFIXES = ("clarinet_test_", "clarinet_e2e_", "clarinet_startup_test")


async def list_test_queues(base_url: str, auth: tuple[str, str]) -> list[str]:
    """List all test queue names via Management API.

    Args:
        base_url: RabbitMQ Management API base URL (e.g. http://host:15672).
        auth: Tuple of (login, password).

    Returns:
        List of test queue names matching known prefixes.
    """
    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        resp = await client.get(f"{base_url}/api/queues/%2F?columns=name")
        resp.raise_for_status()
        return [q["name"] for q in resp.json() if q["name"].startswith(TEST_QUEUE_PREFIXES)]


async def list_test_exchanges(base_url: str, auth: tuple[str, str]) -> list[str]:
    """List all test exchange names via Management API.

    Args:
        base_url: RabbitMQ Management API base URL (e.g. http://host:15672).
        auth: Tuple of (login, password).

    Returns:
        List of test exchange names matching known prefixes.
    """
    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        resp = await client.get(f"{base_url}/api/exchanges/%2F?columns=name")
        resp.raise_for_status()
        return [e["name"] for e in resp.json() if e["name"].startswith(TEST_EXCHANGE_PREFIXES)]


async def get_queue_stats(
    host: str, management_port: int, login: str, password: str
) -> dict[str, int]:
    """Get RabbitMQ queue statistics.

    Args:
        host: RabbitMQ host.
        management_port: Management API port (typically 15672).
        login: Management API login.
        password: Management API password.

    Returns:
        Dict with total_queues, test_queues, total_exchanges, test_exchanges counts.
    """
    base_url = f"http://{host}:{management_port}"
    auth = (login, password)

    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        q_resp = await client.get(f"{base_url}/api/queues/%2F?columns=name,messages,consumers")
        q_resp.raise_for_status()
        queues = q_resp.json()

        e_resp = await client.get(f"{base_url}/api/exchanges/%2F?columns=name")
        e_resp.raise_for_status()
        exchanges = e_resp.json()

    test_queues = [q for q in queues if q["name"].startswith(TEST_QUEUE_PREFIXES)]
    test_exchanges = [e for e in exchanges if e["name"].startswith(TEST_EXCHANGE_PREFIXES)]
    stuck_messages = sum(q.get("messages", 0) for q in test_queues)

    return {
        "total_queues": len(queues),
        "test_queues": len(test_queues),
        "total_exchanges": len(exchanges),
        "test_exchanges": len(test_exchanges),
        "stuck_messages": stuck_messages,
    }


async def cleanup_test_resources(
    host: str,
    management_port: int,
    login: str,
    password: str,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Delete all test queues and exchanges from RabbitMQ.

    Args:
        host: RabbitMQ host.
        management_port: Management API port (typically 15672).
        login: Management API login.
        password: Management API password.
        dry_run: If True, only report what would be deleted.

    Returns:
        Dict with queues_deleted, exchanges_deleted (and *_found for dry_run).
    """
    base_url = f"http://{host}:{management_port}"
    auth = (login, password)

    queues = await list_test_queues(base_url, auth)
    exchanges = await list_test_exchanges(base_url, auth)

    if dry_run:
        for q in queues:
            logger.info(f"[dry-run] Would delete queue: {q}")
        for e in exchanges:
            logger.info(f"[dry-run] Would delete exchange: {e}")
        return {
            "queues_deleted": 0,
            "exchanges_deleted": 0,
            "queues_found": len(queues),
            "exchanges_found": len(exchanges),
        }

    deleted_q = deleted_e = 0
    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        for name in queues:
            encoded = quote(name, safe="")
            try:
                resp = await client.delete(f"{base_url}/api/queues/%2F/{encoded}")
                if resp.status_code in (200, 204, 404):
                    deleted_q += 1
            except Exception:
                logger.warning(f"Failed to delete queue: {name}")

        for name in exchanges:
            encoded = quote(name, safe="")
            try:
                resp = await client.delete(f"{base_url}/api/exchanges/%2F/{encoded}")
                if resp.status_code in (200, 204, 404):
                    deleted_e += 1
            except Exception:
                logger.warning(f"Failed to delete exchange: {name}")

    return {"queues_deleted": deleted_q, "exchanges_deleted": deleted_e}


# Production queue prefix (matches broker.py constants)
_PROD_QUEUE_PREFIX = "clarinet."


async def purge_queue_messages(
    host: str,
    management_port: int,
    login: str,
    password: str,
    queue_names: list[str] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Purge messages from production ``clarinet.*`` queues.

    Removes all messages from specified queues (or all ``clarinet.*``
    queues if none specified) while keeping the queues themselves intact.

    Args:
        host: RabbitMQ host.
        management_port: Management API port (typically 15672).
        login: Management API login.
        password: Management API password.
        queue_names: Specific queues to purge. If ``None``, purges all
            ``clarinet.*`` queues.
        dry_run: If ``True``, only report what would be purged.

    Returns:
        Dict with ``queues_purged`` count and ``messages_purged`` total.
    """
    base_url = f"http://{host}:{management_port}"
    auth = (login, password)

    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        resp = await client.get(f"{base_url}/api/queues/%2F?columns=name,messages")
        resp.raise_for_status()
        all_queues = resp.json()

    if queue_names is not None:
        targets = [q for q in all_queues if q["name"] in queue_names and q.get("messages", 0) > 0]
    else:
        targets = [
            q
            for q in all_queues
            if q["name"].startswith(_PROD_QUEUE_PREFIX)
            and not q["name"].startswith(TEST_QUEUE_PREFIXES)
            and q.get("messages", 0) > 0
        ]

    if dry_run:
        for q in targets:
            logger.info(f"[dry-run] Would purge {q['messages']} messages from: {q['name']}")
        return {
            "queues_purged": 0,
            "messages_found": sum(q.get("messages", 0) for q in targets),
        }

    purged = 0
    total_messages = 0
    async with httpx.AsyncClient(auth=auth, timeout=10) as client:
        for q in targets:
            encoded = quote(q["name"], safe="")
            try:
                resp = await client.delete(f"{base_url}/api/queues/%2F/{encoded}/contents")
                if resp.status_code in (200, 204):
                    purged += 1
                    total_messages += q.get("messages", 0)
                    logger.info(f"Purged {q['messages']} messages from: {q['name']}")
            except Exception:
                logger.warning(f"Failed to purge queue: {q['name']}")

    return {"queues_purged": purged, "messages_purged": total_messages}
