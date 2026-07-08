"""OpsRepo — log_op/log_audit/log_state 기록 + recent_* 조회 + ops locked 증명.

증명:
(a) admin uow 로 log_op/log_audit/log_state → recent_op_logs/recent_state_logs 로 재조회.
    detail(jsonb) 왕복, from_cd None 보존.
(b) ops locked: app 롤(learner uow)로 ops.state_log SELECT 시도 → permission denied
    (ops 는 _APP_SCHEMAS 밖 = 스키마 USAGE 없음). admin uow 는 기록·조회 성공.

ops 테이블은 FK 가 없어(append-only 이력) entity_id/subject_id 는 임의 uuid 로 시드 가능.
실 PG + 페이크 없음(순수 repo). 없으면 skip. test_report_svc.py 픽스처 패턴 재사용.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_ops_repo_test"

pytestmark = pytest.mark.asyncio


def _cfg() -> Settings:
    return Settings(
        env="test",
        db_dsn_app=f"postgresql+asyncpg://sayday_app:sayday_app@localhost:5432/{TEST_DB}",
        db_dsn_admin=f"postgresql+asyncpg://sayday_admin:sayday_admin@localhost:5432/{TEST_DB}",
    )


async def _pg_available() -> bool:
    try:
        engine = create_async_engine(SUPER_DSN, connect_args={"timeout": 10})
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


async def _build_schema() -> None:
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

    database = Db(_cfg())
    await apply_ddl(database.admin_engine)
    await database.dispose()


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def _schema():
    if not asyncio.run(_pg_available()):
        pytest.skip("로컬 PostgreSQL 없음")
    asyncio.run(_build_schema())


@pytest.fixture
async def db(_schema):
    database = Db(_cfg())
    yield database
    await database.dispose()


async def test_log_and_recent_roundtrip(db):
    uowf = SqlUowFactory(db)
    ring_id = uuid.uuid4()
    subject_id = uuid.uuid4()

    async with uowf.admin() as uow:
        await uow.ops.log_op("WORKER", "RING_START", {"ring": str(ring_id)})
        await uow.ops.log_audit("CARROT", subject_id, "LEVEL_CHANGE", {"to": "B1"})
        await uow.ops.log_state("RING", ring_id, None, "SCHEDULED")

    async with uowf.admin() as uow:
        ops = await uow.ops.recent_op_logs()
        states = await uow.ops.recent_state_logs()

    # (a) op_log 왕복 — detail(jsonb) dict 복원
    op = next(o for o in ops if o.action_cd == "RING_START")
    assert op.actor_cd == "WORKER"
    assert op.detail == {"ring": str(ring_id)}

    # (a) state_log 왕복 — from_cd None 보존, entity 매칭
    st = next(s for s in states if s.entity_id == ring_id)
    assert st.entity_cd == "RING"
    assert st.from_cd is None
    assert st.to_cd == "SCHEDULED"

    # audit_log 는 recent 조회 대상 아님 — raw SELECT 로 기록 확인 (admin)
    async with db.admin_uow() as s:
        row = (
            await s.execute(
                text(
                    "SELECT actor_cd, change_cd, detail FROM ops.audit_log "
                    "WHERE subject_id = :sid"
                ),
                {"sid": subject_id},
            )
        ).mappings().one()
    assert row["actor_cd"] == "CARROT"
    assert row["change_cd"] == "LEVEL_CHANGE"
    assert row["detail"] == {"to": "B1"}


async def test_recent_state_logs_honors_limit(db):
    uowf = SqlUowFactory(db)
    # 각각 별도 트랜잭션(=별도 created_ts) 으로 3건 기록 → limit 2 는 2건만
    for to in ("A", "B", "C"):
        async with uowf.admin() as uow:
            await uow.ops.log_state("LIMITTEST", uuid.uuid4(), None, to)

    async with uowf.admin() as uow:
        limited = await uow.ops.recent_state_logs(limit=2)
    subset = [s for s in limited if s.entity_cd == "LIMITTEST"]
    assert len(limited) == 2
    # 최신순 DESC — 마지막 기록 'C' 가 반드시 포함
    assert "C" in {s.to_cd for s in subset}


async def test_ops_locked_app_role_denied_admin_ok(db):
    """ops locked 증명: app 롤은 ops 스키마 도달 불가(USAGE 없음), admin 은 기록·조회 성공."""
    uowf = SqlUowFactory(db)
    ring_id = uuid.uuid4()

    # admin 기록 성공
    async with uowf.admin() as uow:
        await uow.ops.log_state("RING", ring_id, "SCHEDULED", "RINGING")

    # app 롤(learner uow) — ops.state_log SELECT 는 permission denied (도달 불가)
    async with db.learner_uow(uuid.uuid4()) as s:
        with pytest.raises(Exception) as exc:
            await s.execute(text("SELECT id FROM ops.state_log"))
    assert "permission denied" in str(exc.value).lower()

    # app 롤 — INSERT 도 거부 (append 도 admin 전용)
    async with db.learner_uow(uuid.uuid4()) as s:
        with pytest.raises(Exception) as exc:
            await s.execute(
                text(
                    "INSERT INTO ops.state_log (entity_cd, entity_id, to_cd) "
                    "VALUES ('RING', :e, 'ENDED')"
                ),
                {"e": uuid.uuid4()},
            )
    assert "permission denied" in str(exc.value).lower()

    # admin 은 정상 조회 (carrot/worker 경로)
    async with uowf.admin() as uow:
        states = await uow.ops.recent_state_logs()
    assert any(s.entity_id == ring_id and s.to_cd == "RINGING" for s in states)
