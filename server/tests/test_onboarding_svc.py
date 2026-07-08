"""onboarding_svc.complete_onboarding 통합 검증 — 진짜 PostgreSQL 에서 (B1).

증명하는 것:
(a) account.learner.level_cd 가 설정된다 (learner uow 로 조회)
(b) 초기 문형 카드 count개가 learning.pattern_card 에 생성된다
(c) 재호출은 멱등 — 카드 수 불변, uq(learner_id,pattern_key) 충돌 없음

test_repos.py 의 fixture/_pg_available/seed 패턴을 재사용한다.
로컬 postgresql@15 필요 — 없으면 skip.
"""
import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application.onboarding_svc import complete_onboarding
from sayday_server.config import Settings
from sayday_server.infrastructure.catalog import InMemoryCatalog
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_onboarding_svc_test"

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


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
            text(
                "INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) "
                "VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"
            ),
            {"i": identity_id, "k": f"onboarding-{learner_id}@test.io"},
        )
        await s.execute(
            text(
                "INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) "
                "VALUES (:l, :i, 'onboarding', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"
            ),
            {"l": learner_id, "i": identity_id},
        )
    return learner_id


async def test_complete_onboarding_sets_level_and_assigns_cards_idempotently(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    catalog = InMemoryCatalog()

    assigned = await complete_onboarding(uowf, catalog, learner_id, "B1", NOW, count=4)

    expected_keys = await catalog.starter_pool("B1", 4)
    assert assigned == expected_keys

    # (a) account.learner.level_cd 설정됨 — learner uow(RLS 경로)로 조회
    async with uowf.learner(learner_id) as uow:
        level = await uow.account.get_learner_level(learner_id)
    assert level == "B1"

    # (b) learner uow 로 조회 시 pattern_card count(4)개 생성됨
    async with uowf.learner(learner_id) as uow:
        cards = await uow.learning.list_cards(learner_id)
    assert {c.pattern_key for c in cards} == set(expected_keys)
    assert len(cards) == 4
    for card in cards:
        assert card.fsrs_card.due == NOW  # new_pattern_card 로 신규 생성됨

    # (c) 재호출 멱등 — 카드 수 불변(uq 충돌 없음), 배정 키는 동일
    assigned2 = await complete_onboarding(uowf, catalog, learner_id, "B1", NOW, count=4)
    assert assigned2 == expected_keys

    async with uowf.learner(learner_id) as uow:
        cards2 = await uow.learning.list_cards(learner_id)
        level2 = await uow.account.get_learner_level(learner_id)
    assert len(cards2) == 4  # 중복 생성 안 됨
    assert level2 == "B1"


async def test_complete_onboarding_respects_count_cap(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    catalog = InMemoryCatalog()

    assigned = await complete_onboarding(uowf, catalog, learner_id, "A2", NOW, count=2)
    assert assigned == await catalog.starter_pool("A2", 2)

    async with uowf.learner(learner_id) as uow:
        cards = await uow.learning.list_cards(learner_id)
    assert len(cards) == 2  # count 상한 준수
