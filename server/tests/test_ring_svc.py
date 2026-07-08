"""ring_svc — start_ring(발신 개시) + transition_ring(상태 전이).

실 PG + 페이크 게이트웨이(FakeRing/LogPush) + InMemoryCatalog. 없으면 skip.
test_repos.py 의 _pg_available/_build_schema/seed 패턴을 재사용한다.
"""
import asyncio
import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application import ring_svc
from sayday_server.application.errors import InvalidStateError, NotFoundError
from sayday_server.config import Settings
from sayday_server.domain.ring_state import RingStatus
from sayday_server.infrastructure.catalog import InMemoryCatalog
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory
from sayday_server.infrastructure.gateway.fakes import FakeRing, LogPush

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_ring_svc_test"

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)


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


async def _seed_learner(db: Db) -> uuid.UUID:
    identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
    async with db.admin_uow() as s:
        await s.execute(
            text("INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"),
            {"i": identity_id, "k": f"ring-{learner_id}@test.io"},
        )
        await s.execute(
            text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, 'ring', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
            {"l": learner_id, "i": identity_id},
        )
    return learner_id


async def test_start_ring_builds_plan_grants_and_rings(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    push = LogPush()
    ring_port = FakeRing()
    catalog = InMemoryCatalog()
    rng = random.Random(0)

    ring = await ring_svc.start_ring(
        uowf, ring_port, push, catalog, learner_id, None, NOW, rng
    )

    # (1) drill_plan 생성 — 카드 없으니 catalog 신규 도입 1개가 심어짐
    assert ring.drill_plan is not None
    assert len(ring.drill_plan.steps) > 0

    # (2) 상태 = RINGING (SCHEDULED→RINGING 전이)
    assert ring.status_cd == RingStatus.RINGING.value

    # (3) FakeRing grant 가 room_grant_ref 로 저장됨
    assert ring.room_grant_ref == f"fake-{ring.id}"

    # (4) LogPush 발신 트리거 1회 (ring_id 전달)
    assert len(push.sent) == 1
    assert push.sent[0][2] == ring.id

    # (5) 영속화 확인 — 재조회해도 RINGING + grant 유지
    async with uowf.admin() as uow:
        loaded = await uow.call.get_ring(ring.id)
    assert loaded is not None
    assert loaded.status_cd == RingStatus.RINGING.value
    assert loaded.room_grant_ref == f"fake-{ring.id}"


async def test_transition_ring_sets_timestamps(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    ring = await ring_svc.start_ring(
        uowf, FakeRing(), LogPush(), InMemoryCatalog(), learner_id, None, NOW, random.Random(0)
    )

    # RINGING → IN_CALL: started_ts 기록
    t_incall = datetime(2026, 7, 8, 12, 0, 5, tzinfo=UTC)
    after = await ring_svc.transition_ring(uowf, ring.id, RingStatus.IN_CALL, t_incall)
    assert after.status_cd == RingStatus.IN_CALL.value
    assert after.started_ts == t_incall
    assert after.ended_ts is None

    # IN_CALL → ENDED: ended_ts 기록
    t_end = datetime(2026, 7, 8, 12, 3, 0, tzinfo=UTC)
    ended = await ring_svc.transition_ring(uowf, ring.id, RingStatus.ENDED, t_end)
    assert ended.status_cd == RingStatus.ENDED.value
    assert ended.ended_ts == t_end
    assert ended.started_ts == t_incall  # 유지


async def test_transition_ring_illegal_raises(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    ring = await ring_svc.start_ring(
        uowf, FakeRing(), LogPush(), InMemoryCatalog(), learner_id, None, NOW, random.Random(0)
    )
    # RINGING → ENDED 는 불가 (IN_CALL 건너뜀)
    with pytest.raises(InvalidStateError):
        await ring_svc.transition_ring(uowf, ring.id, RingStatus.ENDED, NOW)


async def test_transition_ring_not_found_raises(db):
    uowf = SqlUowFactory(db)
    with pytest.raises(NotFoundError):
        await ring_svc.transition_ring(uowf, uuid.uuid4(), RingStatus.RINGING, NOW)


async def test_state_transitions_are_logged(db):
    """상태로깅 회귀 — start_ring 은 None→SCHEDULED·SCHEDULED→RINGING 을, 이어지는
    transition 은 RINGING→IN_CALL 을 ops.state_log 에 남긴다(같은 admin 트랜잭션 원자)."""
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    ring = await ring_svc.start_ring(
        uowf, FakeRing(), LogPush(), InMemoryCatalog(), learner_id, None, NOW, random.Random(0)
    )
    t_incall = datetime(2026, 7, 8, 12, 0, 5, tzinfo=UTC)
    await ring_svc.transition_ring(uowf, ring.id, RingStatus.IN_CALL, t_incall)

    async with db.admin_uow() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT from_cd, to_cd FROM ops.state_log "
                    "WHERE entity_cd = 'RING' AND entity_id = :r"
                ),
                {"r": ring.id},
            )
        ).all()
    # start_ring 의 두 로그는 같은 트랜잭션이라 created_ts 동일 → 순서 대신 집합으로 검증
    pairs = {(r[0], r[1]) for r in rows}
    assert (None, RingStatus.SCHEDULED.value) in pairs
    assert (RingStatus.SCHEDULED.value, RingStatus.RINGING.value) in pairs
    assert (RingStatus.RINGING.value, RingStatus.IN_CALL.value) in pairs
    assert len(rows) == 3
