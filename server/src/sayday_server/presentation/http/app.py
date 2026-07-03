"""FastAPI 앱 팩토리 — CORS '*' 금지 (링크로어 룰)."""
from __future__ import annotations

from fastapi import FastAPI, Request

from ...application.authz import Principal
from ...config import Settings, settings
from .mw import install_middleware


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or settings()
    app = FastAPI(title="sayday", docs_url=None if cfg.env == "prod" else "/docs")
    install_middleware(app, cfg)

    @app.get("/api/public/health")
    async def health() -> dict:  # 배포 헬스체크 (인증 없음)
        return {"alive_yn": True, "env": cfg.env}

    @app.get("/api/me")
    async def me(request: Request) -> dict:  # 인증 스모크용 최소 보호 라우트
        p: Principal = request.state.principal
        return {
            "identity_id": str(p.identity_id),
            "role": p.role.value,
            "learner_id": str(p.learner_id) if p.learner_id else None,
        }

    @app.get("/api/carrot/ping")
    async def carrot_ping() -> dict:  # 역할 가드 스모크용
        return {"alive_yn": True}

    return app
