"""미들웨어 체인 — 순서 고정 (ARCHITECTURE §7).

request_id → access_log(폴링·GET 성공 스킵) → auth(JWT) → role_guard(prefix↔역할)
에러는 전부 {error_code, message} envelope — raw 500 노출 금지.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ...application.authz import Role, verify_access_token
from ...application.errors import AppError, ForbiddenError, UnauthenticatedError
from ...config import Settings

log = logging.getLogger("sayday.access")

PUBLIC_PREFIX = "/api/public/"

# prefix → 허용 역할 (구체적 prefix 먼저 매칭)
_ROLE_PREFIXES: list[tuple[str, tuple[Role, ...]]] = [
    ("/api/carrot/", (Role.CARROT,)),
    ("/api/potato/", (Role.POTATO, Role.CARROT)),
    ("/api/internal/", (Role.RINGER,)),
    ("/api/", (Role.LEARNER,)),
]


def _envelope(error_code: str, message: str, http_status: int) -> JSONResponse:
    return JSONResponse({"error_code": error_code, "message": message}, status_code=http_status)


class RequestIdMw(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response


class AccessLogMw(BaseHTTPMiddleware):
    """로그 볼륨 최적화 (모하더스): GET 2xx·헬스체크는 스킵."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        skip = (
            request.url.path == "/api/public/health"
            or (request.method == "GET" and response.status_code < 400)
        )
        if not skip:
            log.info(
                "%s %s %s rid=%s",
                request.method, request.url.path, response.status_code,
                getattr(request.state, "request_id", "-"),
            )
        return response


class AuthMw(BaseHTTPMiddleware):
    """Bearer 검증 → request.state.principal. public 경로만 통과."""

    def __init__(self, app: FastAPI, cfg: Settings) -> None:
        super().__init__(app)
        self._cfg = cfg

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path.startswith(PUBLIC_PREFIX) or not path.startswith("/api/"):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            e = UnauthenticatedError()
            return _envelope(e.error_code, e.message, e.http_status)
        try:
            request.state.principal = verify_access_token(self._cfg, header.removeprefix("Bearer "))
        except AppError as e:
            return _envelope(e.error_code, e.message, e.http_status)
        return await call_next(request)


class RoleGuardMw(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path.startswith(PUBLIC_PREFIX) or not path.startswith("/api/"):
            return await call_next(request)
        principal = getattr(request.state, "principal", None)
        if principal is None:  # AuthMw 가 먼저 돌았어야 함
            e = UnauthenticatedError()
            return _envelope(e.error_code, e.message, e.http_status)
        for prefix, allowed in _ROLE_PREFIXES:
            if path.startswith(prefix):
                if principal.role not in allowed:
                    e = ForbiddenError()
                    return _envelope(e.error_code, e.message, e.http_status)
                break
        return await call_next(request)


def install_middleware(app: FastAPI, cfg: Settings) -> None:
    """add_middleware 는 LIFO — 실행 순서의 역순으로 등록한다."""
    app.add_middleware(RoleGuardMw)
    app.add_middleware(AuthMw, cfg=cfg)
    app.add_middleware(AccessLogMw)
    app.add_middleware(RequestIdMw)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:  # pyright: ignore
        return _envelope(exc.error_code, exc.message, exc.http_status)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:  # pyright: ignore
        log.exception("unhandled", exc_info=exc)
        return _envelope("SRV_001", "일시적인 오류가 발생했습니다", 500)
