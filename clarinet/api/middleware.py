"""ASGI middleware for Clarinet API."""

from urllib.parse import parse_qsl, urlencode

from starlette.types import ASGIApp, Receive, Scope, Send


class NullQueryParamMiddleware:
    """Convert literal ``"null"`` query-param values to absent params.

    Many HTTP clients (browsers, JS fetch, API tools) serialize JSON ``null``
    into the query string as the literal string ``"null"``.  FastAPI/Pydantic
    cannot parse ``"null"`` as ``bool``, ``UUID``, ``int``, etc., so the
    request is rejected with 422.

    This middleware strips any query parameter whose value is the string
    ``"null"`` (case-sensitive) before it reaches the router, so FastAPI
    treats the parameter as absent and falls back to its ``None`` default.

    Enable/disable via ``Settings.coerce_null_query_params`` (default ``True``).

    Args:
        app: The ASGI application to wrap.
    """

    __slots__ = ("_app",)

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("query_string"):
            raw_qs: bytes = scope["query_string"]
            filtered = [(k, v) for k, v in parse_qsl(raw_qs.decode("latin-1")) if v != "null"]
            scope["query_string"] = urlencode(filtered).encode("latin-1")

        await self._app(scope, receive, send)
