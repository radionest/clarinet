"""Server-Sent Events stream of entity / task-progress events.

Auth is the ordinary cookie session (``CurrentUserDep``): the stream is a
plain HTTP GET, so an invalid cookie yields a 401 before the stream starts and
``EventSource`` goes to CLOSED without a reconnect storm.
"""

import asyncio
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from clarinet.api.auth_config import DatabaseStrategy
from clarinet.api.dependencies import CurrentUserDep, get_user_role_names
from clarinet.models import User
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.services.events.bus import SseConnection, get_event_bus
from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager

router = APIRouter()
PING_INTERVAL = 30.0


async def _load_allowed_types(user: User) -> set[str]:
    async with db_manager.get_async_session_context() as session:
        return await RecordTypeRepository(session).get_names_for_roles(get_user_role_names(user))


async def _revalidate(token: str | None, request: Request) -> bool:
    """Re-check the session token mid-stream with a fresh short-lived session.

    Pass ``request`` to ``DatabaseStrategy`` so the IP-binding check
    (session_ip_check) stays in force — parity with the production cookie path.
    NB: ``read_token`` always commits ``access_token.last_accessed``, so this is
    NOT read-only — it refreshes idle-timeout state every
    ``sse_revalidate_seconds``; its validation TTL cache
    (``session_cache_ttl_seconds``) can delay revoke detection by up to the TTL.

    Security implication (intentional): because every revalidation refreshes
    ``last_accessed``, an open stream keeps the session non-idle for its whole
    lifetime — a tab left open will not hit ``session_idle_timeout_minutes``.
    This mirrors any other active session and is still bounded by the hard
    ``session_absolute_timeout_days`` cap. A truly idle-respecting revalidation
    would need a read-only token check in the auth layer (out of scope here).
    """
    if not token:
        return False
    async with db_manager.get_async_session_context() as session:
        user = await DatabaseStrategy(session, request).read_token(token, None)  # type: ignore[arg-type]
    return user is not None


@router.get("/events")
async def events_stream(request: Request, user: CurrentUserDep) -> StreamingResponse:
    """SSE stream of entity/task-progress events. Cookie auth via CurrentUserDep.

    HTTP/1.1 caps a browser at ~6 connections per host; each open stream holds a
    slot for its lifetime. Serve over HTTP/2 (nginx) to lift the cap.
    """
    bus = get_event_bus()
    if bus is None:  # sse_enabled=False or lifespan not initialised
        raise HTTPException(status_code=503, detail="SSE unavailable")
    token = request.cookies.get(settings.cookie_name)
    conn = SseConnection(
        user_id=user.id,
        is_admin=user.is_superuser or "admin" in get_user_role_names(user),
        allowed_types=await _load_allowed_types(user),
        queue=asyncio.Queue(maxsize=settings.sse_send_queue_size),
    )

    async def gen() -> AsyncIterator[str]:
        bus.register(conn)
        yield "retry: 3000\n\n"
        next_ping = monotonic() + PING_INTERVAL
        next_reval = monotonic() + settings.sse_revalidate_seconds
        try:
            while True:
                now = monotonic()
                if now >= next_ping:
                    yield 'data: {"type": "ping"}\n\n'
                    next_ping = now + PING_INTERVAL
                if now >= next_reval:
                    if not await _revalidate(token, request):
                        yield 'data: {"type": "auth_expired"}\n\n'
                        return
                    next_reval = now + settings.sse_revalidate_seconds
                timeout = max(0.0, min(next_ping, next_reval) - monotonic())
                try:
                    frame = await asyncio.wait_for(conn.queue.get(), timeout=timeout)
                except TimeoutError:
                    continue
                if frame is None:  # slow-consumer sentinel
                    return
                yield f"data: {frame}\n\n"
                # Match the entity field precisely: '"record_type"' alone also
                # appears inside every record event's "record_type_name", which
                # would reload RBAC on every record frame. to_wire() uses
                # json.dumps default separators, so the key reads `"entity": "...`.
                if '"entity": "record_type"' in frame:  # new types visible immediately
                    conn.allowed_types = await _load_allowed_types(user)
        finally:
            bus.unregister(conn)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: do not buffer this response
            "Connection": "keep-alive",
        },
    )
