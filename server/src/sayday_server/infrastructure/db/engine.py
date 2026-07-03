"""엔진·UoW — DSN 분리 + learner 컨텍스트 주입 (링크로어 UoW 패턴).

- learner_uow(learner_id): app DSN(RLS 적용) + 트랜잭션 지역 GUC 주입.
  코드가 learner_id 필터를 깜빡해도 DB 가 남의 행을 안 내준다.
- admin_uow(): admin DSN(BYPASSRLS). carrot/worker/auth svc 의 명시 경로만 사용.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ...config import Settings


class Db:
    def __init__(self, cfg: Settings) -> None:
        self.app_engine: AsyncEngine = create_async_engine(cfg.db_dsn_app, pool_pre_ping=True)
        self.admin_engine: AsyncEngine = create_async_engine(cfg.db_dsn_admin, pool_pre_ping=True)
        self._app_sessions = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self._admin_sessions = async_sessionmaker(self.admin_engine, expire_on_commit=False)

    @asynccontextmanager
    async def learner_uow(self, learner_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """RLS 적용 트랜잭션. set_config(is_local=true) = SET LOCAL — 커밋/롤백 시 소멸."""
        async with self._app_sessions() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_learner_id', :lid, true)"),
                    {"lid": str(learner_id)},
                )
                yield session

    @asynccontextmanager
    async def admin_uow(self) -> AsyncIterator[AsyncSession]:
        """BYPASSRLS 트랜잭션 — 호출부가 명시적으로 admin 경로임을 드러낸다."""
        async with self._admin_sessions() as session:
            async with session.begin():
                yield session

    async def dispose(self) -> None:
        await self.app_engine.dispose()
        await self.admin_engine.dispose()
