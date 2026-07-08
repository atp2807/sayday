"""BillingRepo round-trip + billing RLS 통합 검증 — 진짜 PostgreSQL 에서 (E5 5a).

증명하는 것:
1. plan: create_plan(admin)→list_active_plans/get_plan round-trip.
2. subscription: create_subscription→get_current(최신)/get_by_pg_ref, set_subscription_status.
3. payment: add_payment→get_payment_by_pg_tx(웹훅 멱등키), pg_tx_ref UNIQUE 재삽입 거부.
4. RLS 소유격리: subscription 은 owner(A)만 보고 B 엔 안 보임.
5. 참조테이블 RLS(핵심): plan 은 learner uow 로 활성행 SELECT 가능하나
   INSERT 는 권한 거부(쓰기는 admin 만); 비활성 plan 은 learner 에게 안 보임(USING active_yn).

test_repos.py / test_rls_integration.py 의 fixture·_pg_available·DSN 패턴을 재사용한다.
로컬 postgresql@15 필요 — 없으면 skip.
"""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl
from sayday_server.infrastructure.db.uow import SqlUowFactory

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_billing_test"

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
    """롤 + 테스트 DB 재생성 + DDL — 모듈당 1회 (test_repos 의 setup 과 동일 내용)."""
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


async def _seed_two_learners(db: Db) -> tuple[uuid.UUID, uuid.UUID]:
    """admin 경로로 identity/learner A·B 생성 (auth 는 admin 전용)."""
    ids = {}
    async with db.admin_uow() as s:
        for name in ("a", "b"):
            identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
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


async def _seed_plan(uowf: SqlUowFactory, plan_key: str = "pro_monthly", price: int = 9900):
    """admin/seed 경로로 요금제 1개 생성 후 반환 (app 롤은 plan INSERT 불가)."""
    async with uowf.admin() as uow:
        return await uow.billing.create_plan(plan_key, "Pro", price, "MONTHLY")


async def test_plan_create_and_read(db):
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, "basic_monthly", 4900)
    assert plan.plan_key == "basic_monthly"
    assert plan.price_amt == 4900
    assert plan.period_cd == "MONTHLY"
    assert plan.active_yn is True

    async with uowf.admin() as uow:
        actives = await uow.billing.list_active_plans()
        got = await uow.billing.get_plan("basic_monthly")
    assert plan.id in {p.id for p in actives}
    assert got is not None and got.id == plan.id
    async with uowf.admin() as uow:
        assert await uow.billing.get_plan("nope") is None


async def test_subscription_roundtrip_and_status(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"sub-{a}")

    async with uowf.learner(a) as uow:
        sub = await uow.billing.create_subscription(a, plan.id, "TRIAL", f"fake-ref-{a}")
    assert sub.status_cd == "TRIAL"
    assert sub.started_ts is None and sub.current_period_end_ts is None

    async with uowf.learner(a) as uow:
        current = await uow.billing.get_current_subscription(a)
        by_ref = await uow.billing.get_subscription_by_pg_ref(f"fake-ref-{a}")
    assert current is not None and current.id == sub.id
    assert by_ref is not None and by_ref.id == sub.id

    period_end = NOW + timedelta(days=30)
    async with uowf.learner(a) as uow:
        await uow.billing.set_subscription_status(
            sub.id, "ACTIVE", started_ts=NOW, current_period_end_ts=period_end
        )
    async with uowf.learner(a) as uow:
        loaded = await uow.billing.get_current_subscription(a)
    assert loaded is not None
    assert loaded.status_cd == "ACTIVE"
    assert loaded.started_ts == NOW
    assert loaded.current_period_end_ts == period_end

    # 상태만 바꾸는 부분 UPDATE 는 started_ts/period 를 유지 (COALESCE)
    async with uowf.learner(a) as uow:
        await uow.billing.set_subscription_status(sub.id, "PAST_DUE")
    async with uowf.learner(a) as uow:
        loaded = await uow.billing.get_current_subscription(a)
    assert loaded is not None
    assert loaded.status_cd == "PAST_DUE"
    assert loaded.started_ts == NOW  # 유지
    assert loaded.current_period_end_ts == period_end  # 유지


async def test_get_current_subscription_returns_latest(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"latest-{a}")

    async with uowf.learner(a) as uow:  # 첫 구독 (별도 트랜잭션 → created_ts 더 이름)
        first = await uow.billing.create_subscription(a, plan.id, "EXPIRED", f"ref1-{a}")
    async with uowf.learner(a) as uow:  # 재구독 (더 최신)
        second = await uow.billing.create_subscription(a, plan.id, "TRIAL", f"ref2-{a}")

    async with uowf.learner(a) as uow:
        current = await uow.billing.get_current_subscription(a)
    assert current is not None
    assert current.id == second.id  # created_ts 최신
    assert current.id != first.id


async def test_payment_idempotency_key(db):
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"pay-{a}")
    async with uowf.learner(a) as uow:
        sub = await uow.billing.create_subscription(a, plan.id, "TRIAL", f"payref-{a}")

    tx = f"pgtx-{a}"
    async with uowf.admin() as uow:  # 웹훅 경로 = admin
        pay = await uow.billing.add_payment(a, sub.id, 9900, "PAID", tx, NOW)
    assert pay.status_cd == "PAID"
    assert pay.amount_amt == 9900
    assert pay.paid_ts == NOW

    async with uowf.admin() as uow:
        got = await uow.billing.get_payment_by_pg_tx(tx)
        missing = await uow.billing.get_payment_by_pg_tx("no-such-tx")
    assert got is not None and got.id == pay.id
    assert missing is None

    # pg_tx_ref UNIQUE → 같은 거래 재삽입 거부 (5b 는 get_payment_by_pg_tx 로 멱등 가드)
    async with uowf.admin() as uow:
        with pytest.raises(Exception) as exc:
            await uow.billing.add_payment(a, sub.id, 9900, "PAID", tx, NOW)
    assert "unique" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()


async def test_subscription_owner_isolation(db):
    a, b = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"iso-{a}")

    async with uowf.learner(a) as uow:  # A 가 자기 구독 생성
        sub = await uow.billing.create_subscription(a, plan.id, "TRIAL", f"iso-ref-{a}")

    async with uowf.learner(a) as uow:  # 주인은 본다
        assert (await uow.billing.get_current_subscription(a)) is not None
    async with uowf.learner(b) as uow:  # 남은 못 본다 (RLS)
        assert (await uow.billing.get_current_subscription(b)) is None
        assert (await uow.billing.get_subscription_by_pg_ref(f"iso-ref-{a}")) is None
    assert sub.learner_id == a


async def test_plan_reference_rls_select_allowed_insert_denied(db):
    """참조테이블 RLS 증명: learner uow 는 활성 plan SELECT 가능, INSERT 는 권한 거부."""
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"ref-{a}", 12000)

    # learner uow: 활성 요금제 SELECT 가능 (plan_read 정책 USING active_yn)
    async with uowf.learner(a) as uow:
        actives = await uow.billing.list_active_plans()
        got = await uow.billing.get_plan(f"ref-{a}")
    assert plan.id in {p.id for p in actives}
    assert got is not None and got.id == plan.id

    # learner uow: plan INSERT 은 권한 없음 → 거부 (쓰기는 admin 만)
    async with uowf.learner(a) as uow:
        with pytest.raises(Exception) as exc:
            await uow.billing.create_plan(f"hack-{a}", "H", 1, "MONTHLY")
    assert "permission denied" in str(exc.value).lower()


async def test_inactive_plan_hidden_from_learner(db):
    """비활성 plan 은 learner 에게 안 보임 — get_plan SQL 은 active 필터 안 하고 RLS 가 막는다."""
    a, _ = await _seed_two_learners(db)
    uowf = SqlUowFactory(db)
    plan = await _seed_plan(uowf, f"dead-{a}")

    # admin(BYPASSRLS) raw UPDATE 로 비활성화
    async with db.admin_uow() as s:
        await s.execute(
            text("UPDATE billing.plan SET active_yn = false WHERE id = :pid"),
            {"pid": plan.id},
        )

    async with uowf.learner(a) as uow:  # learner: RLS USING(active_yn) → 안 보임
        assert (await uow.billing.get_plan(f"dead-{a}")) is None
        assert plan.id not in {p.id for p in await uow.billing.list_active_plans()}
    async with uowf.admin() as uow:  # admin: BYPASSRLS → 여전히 보임
        assert (await uow.billing.get_plan(f"dead-{a}")) is not None
