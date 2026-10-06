"""ASGI middleware: API-key auth, per-key rate limiting, security headers, access log.

Pure ASGI (not ``BaseHTTPMiddleware``) so streaming responses — including the MCP
streamable-http endpoint mounted under the app — pass through untouched.
"""

import json
import logging
import time
import uuid

from app.auth import KeyStore, reset_current_principal, set_current_principal
from app.config import Settings
from app.errors import error_body
from app.plans import get_plan
from app.ratelimit import RateLimiter

logger = logging.getLogger("app.access")

_PUBLIC_PATHS = {"/health", "/ready", "/docs", "/redoc", "/openapi.json", "/favicon.ico", "/accounts/signup"}


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == name:
            return value.decode("latin-1")
    return None


class AuthMiddleware:
    """Authenticate the bearer token, apply the rate limit, and set the principal."""

    def __init__(self, app, *, settings: Settings, store: KeyStore, limiter: RateLimiter) -> None:
        self.app = app
        self.settings = settings
        self.store = store
        self.limiter = limiter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if (
            scope.get("method") == "OPTIONS"
            or not self.settings.require_auth
            or path in _PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/artifacts/")
        ):
            return await self.app(scope, receive, send)

        raw = _header(scope, b"authorization")
        token = None
        if raw:
            parts = raw.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()

        principal = await self.store.verify(token) if token else None
        if principal is None:
            return await self._send(
                send, 401, "unauthenticated",
                "Provide a valid API key: Authorization: Bearer <key>.",
            )

        # A key-level override wins; otherwise the plan's rate limit applies.
        limit = principal.rate_limit or get_plan(principal.plan_code).rate_limit_per_min
        if not await self.limiter.check(principal.key_id, limit):
            return await self._send(
                send, 429, "rate_limited",
                f"Rate limit of {limit} requests per minute exceeded.",
            )

        ctx_token = set_current_principal(principal)
        scope.setdefault("state", {})["principal"] = principal
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_principal(ctx_token)

    @staticmethod
    async def _send(send, status: int, code: str, message: str) -> None:
        body = json.dumps(error_body(code, message)).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"content-security-policy", b"object-src 'none'; frame-ancestors 'none'; base-uri 'self'"),
        ]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + self.headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AccessLogMiddleware:
    """One structured JSON log line per request, with a request id."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        status = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message = {
                    **message,
                    "headers": list(message.get("headers", [])) + [(b"x-request-id", request_id.encode())],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            principal = scope.get("state", {}).get("principal")
            logger.info(
                json.dumps(
                    {
                        "event": "request",
                        "id": request_id,
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status["code"],
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                        "key": getattr(principal, "key_id", None),
                    }
                )
            )
