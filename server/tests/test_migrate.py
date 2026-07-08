"""raw SQL 마이그레이션 러너 검증 — 진짜 PostgreSQL 에서 (없으면 skip).

증명하는 것:
1. 1회차 apply_sql_migrations → migrations/ 의 3개 파일 전부 적용.
2. 2회차 → 0개 (멱등, migration_history 로 skip).
3. migration_history 에 정확히 3행.
4. 이미 적용된 파일의 checksum 이 바뀌면 RuntimeError (불변성 가드).

로컬 postgresql@15 필요 — 없으면 skip. account.learner(FK 대상)만 미리 준비하고
learning/call 은 마이그레이션이 생성하므로, 픽스처는 apply_ddl 대신 auth/account ORM 만 올린다.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.config import Settings
from sayday_server.infrastructure.db.base import Base
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.migrate import apply_sql_migrations

# ORM 메타데이터에 auth/account 테이블 등록 (import 부수효과)
from sayday_server.infrastructure.db import tables_account as _tables_account  # noqa: F401
from sayday_server.infrastructure.db import tables_auth as _tables_auth  # noqa: F401

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_migrate_test"

pytestmark = pytest.mark.asyncio


async def _pg_available() -> bool:
    try:
        engine = create_async_engine(SUPER_DSN)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    if not await _pg_available():
        pytest.skip("로컬 PostgreSQL 없음")
    # 클러스터 준비: 롤 + 테스트 DB 재생성 (superuser, autocommit)
    admin = create_async_engine(SUPER_DSN, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        for stmt in (
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sayday_app') THEN CREATE ROLE sayday_app LOGIN PASSWORD 'sayday_app'; END IF; END $$",
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sayday_admin') THEN CREATE ROLE sayday_admin LOGIN PASSWORD 'sayday_admin' BYPASSRLS; END IF; END $$",
            f"DROP DATABASE IF EXISTS {TEST_DB}",
            f"CREATE DATABASE {TEST_DB} OWNER sayday_admin",
        ):
            await conn.execute(text(stmt))
    await admin.dispose()

    cfg = Settings(
        env="test",
        db_dsn_app=f"postgresql+asyncpg://sayday_app:sayday_app@localhost:5432/{TEST_DB}",
        db_dsn_admin=f"postgresql+asyncpg://sayday_admin:sayday_admin@localhost:5432/{TEST_DB}",
    )
    database = Db(cfg)
    # 마이그레이션은 아직 돌리지 않는다 — auth/account(FK 대상 account.learner)만 준비
    async with database.admin_engine.begin() as conn:
        for schema in ("auth", "account"):
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


async def test_apply_is_idempotent_and_tracked(db):
    async with db.admin_engine.begin() as conn:
        first = await apply_sql_migrations(conn)
    assert first == ["0001_common.sql", "0002_learning.sql", "0003_call.sql"]

    async with db.admin_engine.begin() as conn:
        second = await apply_sql_migrations(conn)
    assert second == []  # 2회차: 전부 skip (멱등)

    async with db.admin_engine.begin() as conn:
        count = (
            await conn.execute(text("SELECT count(*) FROM public.migration_history"))
        ).scalar()
    assert count == 3


async def test_checksum_change_raises(db):
    async with db.admin_engine.begin() as conn:
        applied = await apply_sql_migrations(conn)
    assert len(applied) == 3

    # 기록된 checksum 을 조작 → 파일 실제 내용과 불일치하게 만든다
    async with db.admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.migration_history SET checksum = :bad WHERE filename = :f"
            ),
            {"bad": "0" * 64, "f": "0001_common.sql"},
        )

    with pytest.raises(RuntimeError, match="불변성"):
        async with db.admin_engine.begin() as conn:
            await apply_sql_migrations(conn)


async def test_migrated_tables_enforce_rls(db):
    """마이그레이션 후 learning/call 테이블도 rls_ddl 대상이 되어 소유 격리된다."""
    from sayday_server.infrastructure.db.rls import rls_ddl

    async with db.admin_engine.begin() as conn:
        await apply_sql_migrations(conn)
        for stmt in rls_ddl():
            await conn.execute(text(stmt))

    # admin 으로 learner + 그 소유 pattern_card 1행 심기
    identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
    async with db.admin_uow() as s:
        await s.execute(
            text(
                "INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) VALUES (:i, 'EMAIL', 'm@test.io', 'ACTIVE', now(), now())"
            ),
            {"i": identity_id},
        )
        await s.execute(
            text(
                "INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, 'm', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"
            ),
            {"l": learner_id, "i": identity_id},
        )
        await s.execute(
            text(
                "INSERT INTO learning.pattern_card (id, learner_id, pattern_key, status_cd, fsrs_due_ts, fsrs_stability, fsrs_card, recall_window_ms, created_ts, updated_ts) VALUES (:id, :l, 'used-to', 'ACTIVE', now(), 1.0, '{}'::jsonb, 12000, now(), now())"
            ),
            {"id": uuid.uuid4(), "l": learner_id},
        )

    async with db.learner_uow(learner_id) as s:
        mine = (await s.execute(text("SELECT id FROM learning.pattern_card"))).scalars().all()
    assert len(mine) == 1

    # 위 learner_uow 가 쓴 커넥션이 풀에서 GUC='' 로 복귀 → 에러가 아니라 0행 (NULLIF 가드)
    async with db._app_sessions() as s:  # GUC 없음 → fail-closed
        none = (await s.execute(text("SELECT id FROM learning.pattern_card"))).scalars().all()
    assert none == []
