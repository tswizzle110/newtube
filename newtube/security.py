"""ASGI middleware: authentication gate, same-origin check, security headers.

Written as plain ASGI (not BaseHTTPMiddleware) so long streaming responses such as
video are never buffered or cancelled by the middleware layer.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import COOKIE

# Everything the browser may load must come from this server. No third-party anything.
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "worker-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

PUBLIC_API = {"/api/login", "/api/auth/status", "/api/logout"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class GuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    h[k] = v
            await send(message)

        denied = self._deny(scope)
        if denied:
            await denied(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def _deny(self, scope: Scope) -> JSONResponse | None:
        headers = Headers(scope=scope)
        # CSRF defence in depth (cookie is also SameSite=Strict): state-changing requests
        # must come from this very origin.
        if scope["method"] not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc != headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        path = scope["path"]
        if path.startswith("/api/") and path not in PUBLIC_API:
            auth = scope["app"].state.ctx.auth
            if not auth.check_token(Request(scope).cookies.get(COOKIE)):
                return JSONResponse({"detail": "login required"}, status_code=401)
        return None
