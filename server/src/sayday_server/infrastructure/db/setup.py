"""스키마·테이블·RLS 적용 — dev/test 용 (admin DSN 으로 실행).

프로덕션 마이그레이션은 migrate.py + raw SQL 예정(E2 후속). Alembic 미채택(lr-e2b705eb, 데이터 유실 경험 다수).
도입 시 필수: migration_history 테이블 + UNIQUE(filename) 추적, 모든 적용경로(정규/긴급 모두)가 이 테이블을 갱신,
모든 DDL에 IF NOT EXISTS/IF EXISTS 예외없이(멱등), 실행 전 자동 백업(lr-b087b1a5 배울점/수정할점).
그때도 rls.rls_ddl() 이 정책 SSOT.
롤 생성은 클러스터 레벨이라 여기 없음 → scripts/db_bootstrap.sql (1회).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import Base
from .rls import rls_ddl

# 모델 등록 (metadata 에 테이블 올리기) — import 부수효과 명시
from . import tables_account as _tables_account  # noqa: F401
from . import tables_auth as _tables_auth  # noqa: F401

SCHEMAS = ("auth", "account")


async def apply_ddl(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as conn:
        for schema in SCHEMAS:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)
        for stmt in rls_ddl():
            await conn.execute(text(stmt))
