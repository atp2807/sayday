"""AccountRepo 구현 — account 스키마 전용 (raw SQL, 다른 repo 와 동일 스타일).

learning_repo.py/call_repo.py 처럼 ORM 모델(Learner)이 있어도 raw SQL text() 로
접근한다 (일관성 — E4/B1 결정). learner 소유 컬럼(level_cd)만 다룬다.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SqlAccountRepo:
    """account 스키마 repo — AsyncSession 주입 (UoW 가 공유 세션 전달)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_learner_level(self, learner_id: UUID) -> str | None:
        level = (
            await self._s.execute(
                text("SELECT level_cd FROM account.learner WHERE id = :lid"),
                {"lid": learner_id},
            )
        ).scalar_one_or_none()
        if level is None:
            return None
        assert isinstance(level, str)
        return level

    async def set_learner_level(self, learner_id: UUID, level_cd: str) -> None:
        await self._s.execute(
            text("UPDATE account.learner SET level_cd = :lc WHERE id = :lid"),
            {"lc": level_cd, "lid": learner_id},
        )
