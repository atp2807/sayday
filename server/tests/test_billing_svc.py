"""billing_svc — create_checkout/handle_payment_event/is_entitled. 핵심 테스트 (E5 5b).

증명:
(a) create_checkout: 활성 요금제로 TRIAL 구독 생성 + pg_ref 저장. 없는 plan_key → NotFoundError.
(b) handle_payment_event PAID: ACTIVE 전이 + current_period_end_ts 설정 + payment 1건.
    동일 pg_tx_ref 재적용은 멱등(payment 1건 유지, 상태 재계산 안 됨).
(c) handle_payment_event FAILED: ACTIVE→PAST_DUE + payment FAILED.
(d) is_entitled: ACTIVE(기간 내)=True, 만료=False, 구독없음=False, PAST_DUE=False.

실 PG + FakePay. 없으면 skip.
"""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.application import billing_svc
from sayday_server.application.errors import NotFoundError
from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory
from sayday_server.infrastructure.gateway.fakes import FakePay

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_billing_svc_test"

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
            text(
                "INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) "
                "VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"
            ),
            {"i": identity_id, "k": f"billsvc-{learner_id}@test.io"},
        )
        await s.execute(
            text(
                "INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) "
                "VALUES (:l, :i, 'billsvc', 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"
            ),
            {"l": learner_id, "i": identity_id},
        )
    return learner_id


async def _seed_plan(uowf: SqlUowFactory, plan_key: str, price: int = 9900):
    """admin/seed 경로 — app 롤은 plan INSERT 권한 없음(참조테이블, 5a)."""
    async with uowf.admin() as uow:
        return await uow.billing.create_plan(plan_key, "Pro", price, "MONTHLY")


async def test_create_checkout_creates_trial_subscription(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    plan_key = f"chk-{learner_id}"
    await _seed_plan(uowf, plan_key, 9900)

    session = await billing_svc.create_checkout(uowf, FakePay(), learner_id, plan_key, NOW)

    assert session.pg_ref == f"fake-ref-{learner_id}"
    assert session.checkout_url == f"https://fake.pg/checkout/fake-ref-{learner_id}"

    async with uowf.learner(learner_id) as uow:
        sub = await uow.billing.get_current_subscription(learner_id)
    assert sub is not None
    assert sub.status_cd == "TRIAL"
    assert sub.pg_ref == session.pg_ref


async def test_create_checkout_missing_plan_raises_not_found(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)

    with pytest.raises(NotFoundError):
        await billing_svc.create_checkout(uowf, FakePay(), learner_id, "no-such-plan", NOW)


async def test_handle_payment_event_paid_activates_and_is_idempotent(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"paid-{learner_id}")
    pg_ref = f"paidref-{learner_id}"
    pg_tx_ref = f"tx-{learner_id}"

    async with uowf.learner(learner_id) as uow:
        await uow.billing.create_subscription(learner_id, plan.id, "TRIAL", pg_ref)

    event = FakePay.make_event(pg_ref, pg_tx_ref, "PAID", 9900)
    await billing_svc.handle_payment_event(uowf, event, NOW)

    async with uowf.admin() as uow:
        loaded = await uow.billing.get_subscription_by_pg_ref(pg_ref)
        payment = await uow.billing.get_payment_by_pg_tx(pg_tx_ref)
    assert loaded is not None
    assert loaded.status_cd == "ACTIVE"
    assert loaded.started_ts == NOW
    assert loaded.current_period_end_ts == NOW + timedelta(days=30)
    assert payment is not None
    assert payment.status_cd == "PAID"
    assert payment.paid_ts == NOW

    # 멱등 — 동일 pg_tx_ref 재적용은 no-op (payment 1건 유지, 상태/기간 재계산 안 됨)
    later = NOW + timedelta(days=5)
    await billing_svc.handle_payment_event(uowf, event, later)

    async with db.admin_uow() as s:
        payment_count = (
            await s.execute(
                text("SELECT count(*) FROM billing.payment WHERE pg_tx_ref = :tx"),
                {"tx": pg_tx_ref},
            )
        ).scalar_one()
    async with uowf.admin() as uow:
        loaded2 = await uow.billing.get_subscription_by_pg_ref(pg_ref)
    assert payment_count == 1
    assert loaded2 is not None
    assert loaded2.status_cd == "ACTIVE"
    assert loaded2.current_period_end_ts == NOW + timedelta(days=30)  # later 로 재계산 안 됨


async def test_handle_payment_event_failed_marks_past_due(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"failed-{learner_id}")
    pg_ref = f"failref-{learner_id}"

    async with uowf.learner(learner_id) as uow:
        sub = await uow.billing.create_subscription(learner_id, plan.id, "TRIAL", pg_ref)
    async with uowf.admin() as uow:  # FAILED→PAST_DUE 는 ACTIVE 에서만 허용 — 선결제로 ACTIVE 시딩
        await uow.billing.set_subscription_status(
            sub.id, "ACTIVE", started_ts=NOW, current_period_end_ts=NOW + timedelta(days=30)
        )

    event = FakePay.make_event(pg_ref, f"tx-fail-{learner_id}", "FAILED", 9900)
    await billing_svc.handle_payment_event(uowf, event, NOW)

    async with uowf.admin() as uow:
        loaded = await uow.billing.get_subscription_by_pg_ref(pg_ref)
        payment = await uow.billing.get_payment_by_pg_tx(f"tx-fail-{learner_id}")
    assert loaded is not None
    assert loaded.status_cd == "PAST_DUE"
    assert payment is not None
    assert payment.status_cd == "FAILED"


async def test_is_entitled_states(db):
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)

    # 구독 없음
    assert await billing_svc.is_entitled(uowf, learner_id, NOW) is False

    plan = await _seed_plan(uowf, f"ent-{learner_id}")
    async with uowf.learner(learner_id) as uow:
        sub = await uow.billing.create_subscription(
            learner_id, plan.id, "TRIAL", f"entref-{learner_id}"
        )

    # TRIAL — 아직 ACTIVE 아님
    assert await billing_svc.is_entitled(uowf, learner_id, NOW) is False

    async with uowf.admin() as uow:
        await uow.billing.set_subscription_status(
            sub.id, "ACTIVE", started_ts=NOW, current_period_end_ts=NOW + timedelta(days=30)
        )

    # ACTIVE, 기간 내
    assert await billing_svc.is_entitled(uowf, learner_id, NOW + timedelta(days=1)) is True
    # 만료 (기간 밖)
    assert await billing_svc.is_entitled(uowf, learner_id, NOW + timedelta(days=31)) is False

    async with uowf.admin() as uow:
        await uow.billing.set_subscription_status(sub.id, "PAST_DUE")

    # PAST_DUE
    assert await billing_svc.is_entitled(uowf, learner_id, NOW + timedelta(days=1)) is False


async def test_payment_event_logs_subscription_state(db):
    """상태로깅 회귀 — PAID→ACTIVE 전이 시 ops.state_log 에 SUBSCRIPTION 전이가 기록된다."""
    learner_id = await _seed_learner(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"logpaid-{learner_id}")
    pg_ref = f"logref-{learner_id}"

    async with uowf.learner(learner_id) as uow:
        sub = await uow.billing.create_subscription(learner_id, plan.id, "TRIAL", pg_ref)

    event = FakePay.make_event(pg_ref, f"logtx-{learner_id}", "PAID", 9900)
    await billing_svc.handle_payment_event(uowf, event, NOW)

    async with db.admin_uow() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT from_cd, to_cd FROM ops.state_log "
                    "WHERE entity_cd = 'SUBSCRIPTION' AND entity_id = :e"
                ),
                {"e": sub.id},
            )
        ).all()
    pairs = {(r[0], r[1]) for r in rows}
    assert ("TRIAL", "ACTIVE") in pairs
