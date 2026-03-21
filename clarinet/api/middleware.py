"""ASGI middleware for Clarinet API."""

from urllib.parse import parse_qsl, urlencode

from starlette.types import ASGIApp, Receive, Scope, Send

from clarinet.utils.logger import logger


class NullQueryParamMiddleware:
    """Strip null-like query-param values so FastAPI uses defaults.

    Many HTTP clients serialize JSON ``null`` as the literal string ``"null"``.
    FastAPI/Pydantic cannot parse ``"null"`` as ``bool``, ``UUID``, ``int``,
    etc., causing 422.

    This middleware strips query parameters whose value is the string
    ``"null"`` (case-insensitive) so FastAPI treats them as absent and
    falls back to ``None`` defaults.  Empty strings (``key=``) are
    preserved — they are valid input for endpoints that accept them.
    The query string is only re-encoded when at least one param is removed.

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
            try:
                parsed = parse_qsl(raw_qs.decode(), keep_blank_values=True)
                filtered = [(k, v) for k, v in parsed if v.lower() != "null"]
                if len(filtered) != len(parsed):
                    scope["query_string"] = urlencode(filtered, doseq=True).encode()
            except (ValueError, UnicodeDecodeError):
                logger.debug("Skipping query param normalization for malformed query string")

        await self._app(scope, receive, send)
