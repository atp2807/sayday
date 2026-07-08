"""FastAPI 앱 팩토리 — CORS '*' 금지 (링크로어 룰).

DI: create_app 부팅 시 Db(cfg)+SqlUowFactory 를 만들어 app.state.uowf 에 둔다.
create_async_engine 은 지연(lazy) — 부팅/import 스모크는 DSN 접속 없이 통과하고,
실제 접속은 route 가 uow 를 열 때만 일어난다. lifespan 종료 시 engine 을 dispose.
presentation(합성 루트)이 infra 를 배선하는 건 원칙5 위반이 아니다(서비스만 infra 무지).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request

from ...application import carrot_svc
from ...application.authz import Principal
from ...config import Settings, settings
from ...infrastructure.db.engine import Db
from ...infrastructure.db.uow import SqlUowFactory
from .mw import install_middleware


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or settings()
    db = Db(cfg)  # 지연 엔진 — 여기서 DSN 접속하지 않음
    uowf = SqlUowFactory(db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.dispose()

    app = FastAPI(
        title="sayday",
        docs_url=None if cfg.env == "prod" else "/docs",
        lifespan=lifespan,
    )
    app.state.uowf = uowf
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

    @app.get("/api/carrot/overview")
    async def carrot_overview(request: Request) -> dict[str, Any]:
        # mw._ROLE_PREFIXES 가 /api/carrot 을 CARROT aud 로 이미 강제 (역할 가드).
        result = await carrot_svc.overview(request.app.state.uowf)
        return {
            "learner_count": result.learner_count,
            "rings_by_status": result.rings_by_status,
            "recent_activity": [
                {
                    "entity_cd": s.entity_cd,
                    "entity_id": str(s.entity_id),
                    "from_cd": s.from_cd,
                    "to_cd": s.to_cd,
                    "created_ts": s.created_ts.isoformat(),
                }
                for s in result.recent_activity
            ],
        }

    return app
