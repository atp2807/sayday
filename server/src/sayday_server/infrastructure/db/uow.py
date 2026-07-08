"""SqlUow + SqlUowFactory — 트랜잭션 경계 구현 (engine.Db 위).

engine 의 learner_uow/admin_uow 가 session.begin() 으로 컨텍스트 종료 시 커밋하므로
SqlUow 는 별도 commit() 을 두지 않는다. 하나의 UoW = 하나의 세션 = repo 2개 공유.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from ...application.repos import AccountRepo, CallRepo, LearningRepo, Uow
from .account_repo import SqlAccountRepo
from .call_repo import SqlCallRepo
from .engine import Db
from .learning_repo import SqlLearningRepo


class SqlUow:
    """learning/call/account repo 가 동일 세션(=동일 트랜잭션)을 공유한다.

    속성 타입을 포트(Protocol)로 선언해 Uow 의 가변 멤버 불변성(invariance)에 맞춘다.
    이 대입이 SqlLearningRepo/SqlCallRepo/SqlAccountRepo 의 포트 준수를 mypy 로 검증한다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.learning: LearningRepo = SqlLearningRepo(session)
        self.call: CallRepo = SqlCallRepo(session)
        self.account: AccountRepo = SqlAccountRepo(session)


class SqlUowFactory:
    """learner()=RLS 적용 UoW, admin()=BYPASSRLS UoW (worker/carrot 경로)."""

    def __init__(self, db: Db) -> None:
        self._db = db

    def learner(self, learner_id: uuid.UUID) -> AbstractAsyncContextManager[Uow]:
        return self._learner(learner_id)

    def admin(self) -> AbstractAsyncContextManager[Uow]:
        return self._admin()

    @asynccontextmanager
    async def _learner(self, learner_id: uuid.UUID) -> AsyncIterator[Uow]:
        async with self._db.learner_uow(learner_id) as session:
            yield SqlUow(session)

    @asynccontextmanager
    async def _admin(self) -> AsyncIterator[Uow]:
        async with self._db.admin_uow() as session:
            yield SqlUow(session)
