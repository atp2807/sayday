"""repo/UoW round-trip 통합 검증 — 진짜 PostgreSQL 에서 (E4 2a).

증명하는 것:
1. LearningRepo save_card→get_card/list_cards round-trip. apply_recall 후에도
   FSRS 상태(due/stability/state)·recall_window 가 보존된다.
2. list_due_cards 는 과거 due 만.
3. add_recall→recent_recalls 최신순 (verdict_calc.should_force 와 맞물림).
4. CallRepo ring 라이프사이클: create_ring(drill_plan jsonb round-trip)→get_ring,
   set_ring_status, add_utterance/list_utterances(seq순), set_utterance_verdict,
   add_correction, create_ring_report/get.
5. ring_slot: create→list_active_slots_due(admin), set_slot_next_fire.
6. UowFactory 격리: admin 으로 심고 learner(a)만 보고 learner(b)엔 안 보임 (RLS 재확인).

test_rls_integration.py 의 fixture/_pg_available/DSN 패턴을 재사용한다.
로컬 postgresql@15 필요 — 없으면 skip.
"""
import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.config import Settings
from sayday_server.domain.pattern import (
    DrillPlan,
    ElicitStep,
    RecallEntry,
    StepKind,
    Verdict,
)
from sayday_server.domain.recall_calc import apply_recall, new_pattern_card
from sayday_server.domain.verdict_calc import should_force
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_repos_test"

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
    """롤 + 테스트 DB 재생성 + DDL — 모듈당 1회 (test_rls_integration 의 setup 과 동일 내용).

    DROP/CREATE DATABASE 는 즉시 체크포인트를 강제하므로(공유 로컬 PG 부하) 테스트마다가
    아니라 모듈당 1회만 돈다. 각 테스트는 fresh uuid learner 를 seed 해 서로 격리된다.
    """
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
    # 격리된 loop(asyncio.run)에서 가용성 확인 + 1회 DDL — 테스트 loop 와 엔진 공유 안 함.
    if not asyncio.run(_pg_available()):
        pytest.skip("로컬 PostgreSQL 없음")
    asyncio.run(_build_schema())


@pytest.fixture
async def db(_schema):
    database = Db(_cfg())
    yield database
    await database.dispose()


async def _seed_two_learners(db: Db) -> tuple[uuid.UUID, uuid.UUID]:
    """admin 경로로 identity/learner A·B 생성 (auth 는 admin 전용)."""
    ids = {}
    async with db.admin_uow() as s:
        for name in ("a", "b"):
            identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
            # login_key 를 호출마다 유니크하게 (DB 를 모듈당 1회만 재생성 → uq ix_identity_login 충돌 방지)
            await s.execute(
                text("INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"),
                {"i": identity_id, "k": f"{name}-{learner_id}@test.io"},
            )
            await s.execute(
                text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, :n, 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
                {"l": learner_id, "i": identity_id, "n": name},
            )
            ids[name] = learner_id
    return ids["a"], ids["b"]


NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)


async def test_card_roundtrip_preserves_fsrs_and_window(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    card = new_pattern_card("used_to", NOW)
    updated = apply_recall(card, Verdict.USED, 2000, NOW).card  # window shrinks, fsrs advances
    assert updated.recall_window_ms != card.recall_window_ms  # apply_recall 실제로 바꿈

    async with uowf.learner(a) as uow:
        cid = await uow.learning.save_card(a, updated)
    assert isinstance(cid, uuid.UUID)

    async with uowf.learner(a) as uow:
        loaded = await uow.learning.get_card(a, "used_to")
        cards = await uow.learning.list_cards(a)

    assert loaded is not None
    assert loaded.recall_window_ms == updated.recall_window_ms
    assert loaded.fsrs_card.due == updated.fsrs_card.due
    assert loaded.fsrs_card.stability == updated.fsrs_card.stability
    assert loaded.fsrs_card.state == updated.fsrs_card.state
    assert loaded.fsrs_card.difficulty == updated.fsrs_card.difficulty
    assert [c.pattern_key for c in cards] == ["used_to"]


async def test_save_card_upserts_on_conflict(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    card = new_pattern_card("used_to", NOW)
    async with uowf.learner(a) as uow:
        id1 = await uow.learning.save_card(a, card)

    reviewed = apply_recall(card, Verdict.USED, 1000, NOW).card
    async with uowf.learner(a) as uow:
        id2 = await uow.learning.save_card(a, reviewed)

    assert id1 == id2  # 같은 (learner, pattern_key) → 같은 row
    async with uowf.learner(a) as uow:
        cards = await uow.learning.list_cards(a)
        loaded = await uow.learning.get_card(a, "used_to")
    assert len(cards) == 1  # 중복 안 생김
    assert loaded is not None
    assert loaded.recall_window_ms == reviewed.recall_window_ms  # 최신값 반영


async def test_list_due_cards_only_past(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    past = new_pattern_card("used_to", NOW)  # due = NOW
    future = new_pattern_card("conditional_perfect", NOW)
    future.fsrs_card.due = NOW + timedelta(days=5)

    async with uowf.learner(a) as uow:
        await uow.learning.save_card(a, past)
        await uow.learning.save_card(a, future)

    async with uowf.learner(a) as uow:
        due = await uow.learning.list_due_cards(a, NOW)
    assert [c.pattern_key for c in due] == ["used_to"]


async def test_add_recall_recent_order_and_force(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    card = new_pattern_card("used_to", NOW)
    async with uowf.learner(a) as uow:
        cid = await uow.learning.save_card(a, card)
        # 시간순: USED(t0) → AVOIDED(t1) → AVOIDED(t2)
        await uow.learning.add_recall(
            a, cid, None, RecallEntry("used_to", Verdict.USED, 1500, NOW), "Good"
        )
        await uow.learning.add_recall(
            a, cid, None,
            RecallEntry("used_to", Verdict.AVOIDED, None, NOW + timedelta(minutes=1)), "Again",
        )
        await uow.learning.add_recall(
            a, cid, None,
            RecallEntry("used_to", Verdict.AVOIDED, None, NOW + timedelta(minutes=2)), "Again",
        )

    async with uowf.learner(a) as uow:
        recents = await uow.learning.recent_recalls(a, "used_to")

    # 최신순: 두 AVOIDED 먼저, 그다음 USED
    assert [e.verdict for e in recents] == [Verdict.AVOIDED, Verdict.AVOIDED, Verdict.USED]
    assert should_force(recents) is True  # AVOIDED 2연속 → 강제


async def test_ring_lifecycle_and_drill_plan_roundtrip(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    plan = DrillPlan(steps=(
        ElicitStep("used_to", StepKind.REVIEW, 9000),
        ElicitStep("conditional_perfect", StepKind.NEW, 12000),
        ElicitStep("used_to", StepKind.REVIEW, 9000),
    ))

    async with uowf.learner(a) as uow:
        ring = await uow.call.create_ring(a, None, plan, None, NOW, "SCHEDULED")
    ring_id = ring.id
    assert ring.status_cd == "SCHEDULED"
    assert ring.drill_plan == plan  # 반환값도 round-trip

    async with uowf.learner(a) as uow:
        loaded = await uow.call.get_ring(ring_id)
    assert loaded is not None
    assert loaded.drill_plan == plan  # jsonb round-trip 무손실

    started = NOW + timedelta(seconds=5)
    async with uowf.learner(a) as uow:
        await uow.call.set_ring_status(ring_id, "RINGING", started_ts=started)
    async with uowf.learner(a) as uow:
        loaded = await uow.call.get_ring(ring_id)
    assert loaded is not None
    assert loaded.status_cd == "RINGING"
    assert loaded.started_ts == started
    assert loaded.ended_ts is None  # 안 넘긴 값은 유지

    async with uowf.learner(a) as uow:
        u1 = await uow.call.add_utterance(
            ring_id, a, 1, "TUTOR", "VOICE", "What did you used to do?", "used_to", None
        )
        await uow.call.add_utterance(
            ring_id, a, 2, "LEARNER", "VOICE", "I used to swim.", "used_to", 1400
        )
    async with uowf.learner(a) as uow:
        utts = await uow.call.list_utterances(ring_id)
    assert [u.seq for u in utts] == [1, 2]  # seq 순
    assert utts[0].verdict_cd is None

    async with uowf.learner(a) as uow:
        await uow.call.set_utterance_verdict(u1.id, "USED")
        await uow.call.add_correction(
            ring_id, a, u1.id, "GRAMMAR", "used to did", "used to do", "base verb after used to"
        )
    async with uowf.learner(a) as uow:
        utts = await uow.call.list_utterances(ring_id)
    assert utts[0].verdict_cd == "USED"

    metrics = {"used": 1, "attempted": 0, "avoided": 0}
    async with uowf.learner(a) as uow:
        report = await uow.call.create_ring_report(ring_id, a, "good session", metrics)
    assert report.metrics == metrics
    async with uowf.learner(a) as uow:
        loaded_report = await uow.call.get_ring_report(ring_id)
    assert loaded_report is not None
    assert loaded_report.summary == "good session"
    assert loaded_report.metrics == metrics


async def test_ring_slot_create_and_list_due(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    past_fire = NOW - timedelta(minutes=1)
    async with uowf.learner(a) as uow:
        slot = await uow.call.create_ring_slot(a, 0b0111110, time(9, 0), "Asia/Seoul", past_fire)
    assert slot.active_yn is True
    assert slot.next_fire_ts == past_fire

    # worker=admin 경로: 발신 대상 슬롯 조회
    async with uowf.admin() as uow:
        due = await uow.call.list_active_slots_due(NOW)
    assert slot.id in {s.id for s in due}

    future_fire = NOW + timedelta(days=1)
    async with uowf.admin() as uow:
        await uow.call.set_slot_next_fire(slot.id, future_fire)
    async with uowf.admin() as uow:
        due = await uow.call.list_active_slots_due(NOW)
    assert slot.id not in {s.id for s in due}  # 미래로 밀림 → 대상 아님


async def test_uow_learner_isolation(db):
    a, b = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)

    card = new_pattern_card("used_to", NOW)
    async with uowf.admin() as uow:  # admin(BYPASSRLS)으로 A 소유 카드 심기
        await uow.learning.save_card(a, card)

    async with uowf.learner(a) as uow:  # 주인은 본다
        assert await uow.learning.get_card(a, "used_to") is not None
        assert len(await uow.learning.list_cards(a)) == 1

    async with uowf.learner(b) as uow:  # 남은 못 본다 (RLS)
        assert await uow.learning.get_card(b, "used_to") is None
        assert await uow.learning.list_cards(b) == []
