"""carrot_svc.overview — 운영 대시보드 집계 정확성 (§5).

증명: 학습자 3명 + ring 4건(SCHEDULED×2/RINGING×1/ENDED×1) + state_log 2건 시드 →
overview 가 learner_count/rings_by_status/recent_activity 를 정확히 집계(admin, cross-schema).

실 PG. 모듈 DB 는 매번 재생성되고 이 파일이 유일 시더라 카운트는 정확값으로 단언한다.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application import carrot_svc
from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_carrot_svc_test"

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


async def _seed_learner(db: Db, tag: str) -> uuid.UUID:
    identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
    async with db.admin_uow() as s:
        await s.execute(
            text("INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"),
            {"i": identity_id, "k": f"carrot-{tag}-{learner_id}@test.io"},
        )
        await s.execute(
            text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, :n, 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
            {"l": learner_id, "i": identity_id, "n": tag},
        )
    return learner_id


async def _seed_ring(db: Db, learner_id: uuid.UUID, status_cd: str) -> None:
    async with db.admin_uow() as s:
        await s.execute(
            text(
                "INSERT INTO call.ring (id, learner_id, status_cd, scheduled_ts, created_ts, updated_ts) "
                "VALUES (:id, :l, :st, now(), now(), now())"
            ),
            {"id": uuid.uuid4(), "l": learner_id, "st": status_cd},
        )


async def test_overview_aggregates_accurately(db):
    uowf = SqlUowFactory(db)
    learners = [await _seed_learner(db, t) for t in ("a", "b", "c")]

    # ring 4건: SCHEDULED×2, RINGING×1, ENDED×1
    await _seed_ring(db, learners[0], "SCHEDULED")
    await _seed_ring(db, learners[0], "SCHEDULED")
    await _seed_ring(db, learners[1], "RINGING")
    await _seed_ring(db, learners[2], "ENDED")

    # state_log 2건 (별도 트랜잭션 = 별도 created_ts, 최신순 검증 가능)
    e1, e2 = uuid.uuid4(), uuid.uuid4()
    async with uowf.admin() as uow:
        await uow.ops.log_state("RING", e1, None, "SCHEDULED")
    async with uowf.admin() as uow:
        await uow.ops.log_state("RING", e2, "SCHEDULED", "RINGING")

    result = await carrot_svc.overview(uowf, limit=20)

    # 학습자 수 정확
    assert result.learner_count == 3

    # ring 상태 분포 정확
    assert result.rings_by_status == {"SCHEDULED": 2, "RINGING": 1, "ENDED": 1}

    # 최근 상태전이 이력 — 기록한 2건, 최신(e2)이 앞
    assert len(result.recent_activity) == 2
    activity_ids = [s.entity_id for s in result.recent_activity]
    assert set(activity_ids) == {e1, e2}
    assert result.recent_activity[0].entity_id == e2  # DESC — 마지막 기록이 최신
    assert result.recent_activity[0].to_cd == "RINGING"
    assert result.recent_activity[0].from_cd == "SCHEDULED"


async def test_carrot_overview_endpoint(db):
    """GET /api/carrot/overview — carrot 토큰으로 200 + 스펙 shape (실PG, route-DI).

    이 테스트는 test_overview_aggregates_accurately 이후 실행되어 카운트가 누적되므로
    정확값 대신 하한(>=)으로 shape 만 검증한다(집계 정확성은 서비스 테스트가 담당).
    """
    from fastapi.testclient import TestClient

    from sayday_server.application.authz import Role, issue_access_token
    from sayday_server.presentation.http.app import create_app

    cfg = _cfg()
    learner_id = await _seed_learner(db, "ep")
    await _seed_ring(db, learner_id, "SCHEDULED")
    uowf = SqlUowFactory(db)
    async with uowf.admin() as uow:
        await uow.ops.log_state("RING", uuid.uuid4(), None, "SCHEDULED")

    token = issue_access_token(cfg, uuid.uuid4(), Role.CARROT)
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        r = client.get(
            "/api/carrot/overview", headers={"authorization": f"Bearer {token}"}
        )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["learner_count"], int) and body["learner_count"] >= 1
    assert body["rings_by_status"].get("SCHEDULED", 0) >= 1
    assert isinstance(body["recent_activity"], list) and len(body["recent_activity"]) >= 1
    item = body["recent_activity"][0]
    assert set(item) == {"entity_cd", "entity_id", "from_cd", "to_cd", "created_ts"}


async def test_carrot_overview_endpoint_rejects_learner_token(db):
    """역할 가드 회귀 — learner 토큰은 /api/carrot/overview 에 403 (DB 도달 전 거부)."""
    from fastapi.testclient import TestClient

    from sayday_server.application.authz import Role, issue_access_token
    from sayday_server.presentation.http.app import create_app

    cfg = _cfg()
    token = issue_access_token(cfg, uuid.uuid4(), Role.LEARNER, learner_id=uuid.uuid4())
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        r = client.get(
            "/api/carrot/overview", headers={"authorization": f"Bearer {token}"}
        )
    assert r.status_code == 403
