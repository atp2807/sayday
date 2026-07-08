"""schedule_svc — _compute_next_fire 순수 로직 + create_ring_slot round-trip.

_compute_next_fire 는 PG 불필요(순수). create_ring_slot 은 실 PG 필요 — 없으면 skip.
test_repos.py 의 _pg_available/_build_schema/seed 패턴을 재사용한다.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application.schedule_svc import (
    _compute_next_fire,
    create_ring_slot,
    list_due_slots,
)
from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_schedule_test"

# asyncio_mode="auto" 가 async 테스트를 자동 처리 — 동기 순수 테스트가 섞여 있어
# 모듈 레벨 asyncio 마크를 두지 않는다(동기 테스트 경고 방지).

SEOUL = ZoneInfo("Asia/Seoul")


# ── _compute_next_fire — 순수(PG 불필요) ──────────────────────────────


def test_next_fire_same_day_future():
    # 2026-07-08 은 수요일(weekday 2). 오늘 10:00 KST, 대상=수요일, 14:00 → 오늘 14:00.
    now = datetime(2026, 7, 8, 10, 0, tzinfo=SEOUL).astimezone(UTC)
    mask = 1 << 2  # 수요일
    nf = _compute_next_fire(mask, time(14, 0), "Asia/Seoul", now)
    assert nf.utcoffset() == timedelta(0)          # UTC 로 환산돼 반환
    assert nf.astimezone(SEOUL).date() == date(2026, 7, 8)
    assert nf.astimezone(SEOUL).hour == 14
    assert nf.hour == 5                             # 14:00 KST == 05:00 UTC


def test_next_fire_same_day_past_rolls_to_next_week():
    # 오늘(수) 10:00 인데 대상 시각 08:00 이미 지남 → 다음 수요일(+7일).
    now = datetime(2026, 7, 8, 10, 0, tzinfo=SEOUL).astimezone(UTC)
    mask = 1 << 2
    nf = _compute_next_fire(mask, time(8, 0), "Asia/Seoul", now)
    assert nf.astimezone(SEOUL).date() == date(2026, 7, 15)  # 다음 수요일


def test_next_fire_picks_nearest_of_multiple_days():
    # 수(10:00 현재). 대상=월·금. 가장 가까운 대상은 금요일(2026-07-10).
    now = datetime(2026, 7, 8, 10, 0, tzinfo=SEOUL).astimezone(UTC)
    mask = (1 << 0) | (1 << 4)  # 월, 금
    nf = _compute_next_fire(mask, time(9, 0), "Asia/Seoul", now)
    assert nf.astimezone(SEOUL).date() == date(2026, 7, 10)  # 금요일


def test_next_fire_empty_mask_raises():
    now = datetime(2026, 7, 8, 10, 0, tzinfo=SEOUL).astimezone(UTC)
    with pytest.raises(ValueError):
        _compute_next_fire(0, time(9, 0), "Asia/Seoul", now)


# ── create_ring_slot — 실 PG round-trip ──────────────────────────────


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
            {"i": identity_id, "k": f"sched-{learner_id}@test.io"},
        )
        await s.execute(
            text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, 'sched', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
            {"l": learner_id, "i": identity_id},
        )
    return learner_id


async def test_create_ring_slot_persists_computed_next_fire(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)

    now = datetime(2026, 7, 8, 10, 0, tzinfo=SEOUL).astimezone(UTC)
    mask = 1 << 2  # 수요일 → 오늘 14:00
    expected = _compute_next_fire(mask, time(14, 0), "Asia/Seoul", now)

    slot = await create_ring_slot(uowf, learner_id, mask, time(14, 0), "Asia/Seoul", now)
    assert slot.active_yn is True
    assert slot.next_fire_ts == expected
    assert slot.days_of_week == mask

    # 미래 발신이라 지금은 due 아님
    async with uowf.admin() as uow:
        due_now = await uow.call.list_active_slots_due(now)
    assert slot.id not in {s.id for s in due_now}

    # next_fire 이후로 시계를 옮기면 due 로 잡힌다 (list_due_slots = admin 경로)
    later = expected + timedelta(minutes=1)
    due_later = await list_due_slots(uowf, later)
    assert slot.id in {s.id for s in due_later}
