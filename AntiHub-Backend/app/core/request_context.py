"""
Request-scoped context helpers.

用于把当前请求的一些信息（例如请求头）放到 contextvars 里，避免在各个路由里重复传参。
注意：
- 只做“暂存”，真正写入数据库时仍需要脱敏/截断（见 UsageLogService）。
- 采用 ASGI middleware（非 BaseHTTPMiddleware），避免影响 StreamingResponse（SSE）。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Dict, Optional


_request_headers_var: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "request_headers",
    default=None,
)


def get_request_headers() -> Optional[Dict[str, str]]:
    return _request_headers_var.get()


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers: Optional[Dict[str, str]]
        try:
            headers = {
                k.decode("latin-1"): v.decode("latin-1")
                for k, v in (scope.get("headers") or [])
            }
        except Exception:
            headers = None

        token = _request_headers_var.set(headers)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_headers_var.reset(token)

