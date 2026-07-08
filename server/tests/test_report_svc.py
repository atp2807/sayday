"""report_svc.finalize_ring — A6→A8 파이프라인 (루프의 심장). 핵심 테스트.

증명:
(a) 학습자 발화 verdict_cd 채워짐
(b) pattern_card FSRS/recall_window 갱신 (apply_recall 반영)
(c) recall_entry 생성 (연속 AVOIDED → should_force)
(d) ring_report + correction 생성
(e) ring status = REPORTED
(f) 재호출 멱등 (두번째 finalize = no-op)

실 PG + FakeTutor(verdict_by_text). 없으면 skip.
"""
import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application import report_svc
from sayday_server.config import Settings
from sayday_server.domain.pattern import DrillPlan, ElicitStep, StepKind, Verdict
from sayday_server.domain.recall_calc import WINDOW_START_MS
from sayday_server.domain.ring_state import RingStatus
from sayday_server.domain.verdict_calc import should_force
from sayday_server.infrastructure.catalog import InMemoryCatalog
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory
from sayday_server.infrastructure.gateway.fakes import FakeTutor

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_report_svc_test"

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
            {"i": identity_id, "k": f"report-{learner_id}@test.io"},
        )
        await s.execute(
            text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, 'report', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
            {"l": learner_id, "i": identity_id},
        )
    return learner_id


async def _seed_ended_ring(db: Db, learner_id: uuid.UUID) -> uuid.UUID:
    """ENDED 상태 ring + 발화 6개(TUTOR/LEARNER 3쌍). used_to=USED, conditional=AVOIDED×2."""
    uowf = SqlUowFactory(db)
    plan = DrillPlan(steps=(
        ElicitStep("used_to", StepKind.REVIEW, WINDOW_START_MS),
        ElicitStep("conditional_perfect", StepKind.NEW, WINDOW_START_MS),
    ))
    async with uowf.admin() as uow:
        ring = await uow.call.create_ring(
            learner_id, None, plan, None, NOW, RingStatus.ENDED.value
        )
        rid = ring.id
        # seq순: TUTOR 질문 → LEARNER 답변 3쌍
        await uow.call.add_utterance(rid, learner_id, 1, "TUTOR", "VOICE", "What did you used to do?", None, None)
        await uow.call.add_utterance(rid, learner_id, 2, "LEARNER", "VOICE", "I used to swim.", "used_to", 1400)
        await uow.call.add_utterance(rid, learner_id, 3, "TUTOR", "VOICE", "What would you have done?", None, None)
        await uow.call.add_utterance(rid, learner_id, 4, "LEARNER", "VOICE", "avoid-a", "conditional_perfect", None)
        await uow.call.add_utterance(rid, learner_id, 5, "TUTOR", "VOICE", "And if you had known?", None, None)
        await uow.call.add_utterance(rid, learner_id, 6, "LEARNER", "VOICE", "avoid-b", "conditional_perfect", None)
    return rid


def _tutor() -> FakeTutor:
    return FakeTutor(verdict_by_text={"avoid-a": Verdict.AVOIDED, "avoid-b": Verdict.AVOIDED})


async def test_finalize_ring_full_pipeline_and_idempotent(db):
    learner_id = await _seed_learner(db)
    rid = await _seed_ended_ring(db, learner_id)
    uowf = SqlUowFactory(db)
    catalog = InMemoryCatalog()

    await report_svc.finalize_ring(uowf, _tutor(), catalog, rid, NOW)

    async with uowf.admin() as uow:
        utts = await uow.call.list_utterances(rid)
        card_used = await uow.learning.get_card(learner_id, "used_to")
        card_cond = await uow.learning.get_card(learner_id, "conditional_perfect")
        recents_cond = await uow.learning.recent_recalls(learner_id, "conditional_perfect")
        recents_used = await uow.learning.recent_recalls(learner_id, "used_to")
        report = await uow.call.get_ring_report(rid)
        ring = await uow.call.get_ring(rid)

    verdicts = {u.seq: u.verdict_cd for u in utts}
    # (a) 학습자 발화 verdict_cd 채워짐, TUTOR 발화는 안 채워짐
    assert verdicts[2] == "USED"
    assert verdicts[4] == "AVOIDED"
    assert verdicts[6] == "AVOIDED"
    assert verdicts[1] is None and verdicts[3] is None and verdicts[5] is None

    # (b) FSRS/recall_window 갱신 — used_to USED(1400ms) → window 12000→9600
    assert card_used is not None
    assert card_used.recall_window_ms == round(WINDOW_START_MS * 0.8)  # 9600
    assert card_used.fsrs_card.due > NOW  # FSRS 가 다음 due 를 미래로 밀었다
    assert card_cond is not None  # 신규 문형 카드 생성됨

    # (c) recall_entry 생성 + 연속 AVOIDED → should_force
    assert len(recents_used) == 1
    assert len(recents_cond) == 2
    assert [e.verdict for e in recents_cond] == [Verdict.AVOIDED, Verdict.AVOIDED]
    assert should_force(recents_cond) is True

    # (d) ring_report + correction 생성 (FakeTutor: LEARNER 라인당 1 correction = 3개)
    assert report is not None
    assert report.summary == "[fake] summary"
    async with db.admin_uow() as s:
        corr_count = (
            await s.execute(
                text("SELECT count(*) FROM call.correction WHERE ring_id = :r"),
                {"r": rid},
            )
        ).scalar_one()
    assert corr_count == 3

    # (e) ring status = REPORTED
    assert ring is not None
    assert ring.status_cd == RingStatus.REPORTED.value

    # (f) 재호출 멱등 — no-op (에러 없음, report/recall 중복 없음)
    await report_svc.finalize_ring(uowf, _tutor(), catalog, rid, NOW)
    async with uowf.admin() as uow:
        recents_cond2 = await uow.learning.recent_recalls(learner_id, "conditional_perfect")
        report2 = await uow.call.get_ring_report(rid)
    async with db.admin_uow() as s:
        report_count = (
            await s.execute(
                text("SELECT count(*) FROM call.ring_report WHERE ring_id = :r"),
                {"r": rid},
            )
        ).scalar_one()
    assert len(recents_cond2) == 2       # recall_entry 중복 안 생김
    assert report2 is not None
    assert report2.id == report.id       # 같은 리포트 (재생성 안 함)
    assert report_count == 1


async def test_finalize_ring_not_found_raises(db):
    from sayday_server.application.errors import NotFoundError

    uowf = SqlUowFactory(db)
    with pytest.raises(NotFoundError):
        await report_svc.finalize_ring(uowf, _tutor(), InMemoryCatalog(), uuid.uuid4(), NOW)


async def test_finalize_logs_reported_state(db):
    """상태로깅 회귀 — finalize_ring 은 ENDED→REPORTED 전이를 ops.state_log 에 남긴다."""
    learner_id = await _seed_learner(db)
    rid = await _seed_ended_ring(db, learner_id)
    uowf = SqlUowFactory(db)

    await report_svc.finalize_ring(uowf, _tutor(), InMemoryCatalog(), rid, NOW)

    async with db.admin_uow() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT from_cd, to_cd FROM ops.state_log "
                    "WHERE entity_cd = 'RING' AND entity_id = :r"
                ),
                {"r": rid},
            )
        ).all()
    pairs = {(r[0], r[1]) for r in rows}
    assert (RingStatus.ENDED.value, RingStatus.REPORTED.value) in pairs
