"""스키마·테이블·RLS 적용 — dev/test 용 (admin DSN 으로 실행).

auth/account 는 ORM(Base.metadata) 이, learning/call 은 raw SQL 마이그레이션(migrate.py)이 SSOT.
프로덕션 마이그레이션 진입점은 migrate.migrate() (백업 선행 + migration_history 추적). Alembic 미채택(lr-e2b705eb).
migration_history + UNIQUE(filename) 추적, 모든 DDL 멱등(IF NOT EXISTS), 실행 전 자동 백업(lr-b087b1a5)은 migrate.py 가 강제.
rls.rls_ddl() 이 권한/정책 SSOT — 여기·migrate 양쪽에서 재적용(멱등).
롤 생성은 클러스터 레벨이라 여기 없음 → scripts/db_bootstrap.sql (1회).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import Base
from .migrate import apply_sql_migrations
from .rls import rls_ddl

# 모델 등록 (metadata 에 테이블 올리기) — import 부수효과 명시
from . import tables_account as _tables_account  # noqa: F401
from . import tables_auth as _tables_auth  # noqa: F401

# ORM 이 소유하는 스키마만 (learning/call 은 raw SQL 마이그레이션이 생성)
SCHEMAS = ("auth", "account")


async def apply_ddl(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as conn:
        for schema in SCHEMAS:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)
        # learning/call 스키마·테이블·트리거·FK 는 raw SQL 마이그레이션이 SSOT
        await apply_sql_migrations(conn)
        for stmt in rls_ddl():
            await conn.execute(text(stmt))
