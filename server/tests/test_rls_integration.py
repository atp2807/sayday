"""RLS fail-closed 통합 검증 — 진짜 PostgreSQL 에서 (ARCHITECTURE §2 레이어2).

증명하는 것:
1. learner A 컨텍스트에서 B 의 행이 안 보인다 (0행)
2. GUC 미설정(컨텍스트 없는 앱 세션) = 아무것도 안 보인다 (fail-closed)
3. 앱 DSN 은 auth 스키마에 도달 자체가 불가 (자격증명 격리)
4. 앱 DSN 은 DELETE 권한이 없다 (하드삭제 금지를 DB 가 강제)
5. admin DSN(BYPASSRLS) 은 전부 본다 (carrot/worker 경로)
6. 타인 행 INSERT/UPDATE 시도는 WITH CHECK 에 막힌다

로컬 postgresql@15 필요 — 없으면 skip.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sayday_server.config import Settings
from sayday_server.infrastructure.db.engine import Db
from sayday_server.infrastructure.db.setup import apply_ddl

SUPER_DSN = "postgresql+asyncpg://daviy@localhost:5432/postgres"
TEST_DB = "sayday_rls_test"

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
    await apply_ddl(database.admin_engine)
    yield database
    await database.dispose()


async def _seed_two_learners(db: Db) -> tuple[uuid.UUID, uuid.UUID]:
    """admin 경로로 identity/learner A·B 생성 (auth 는 admin 전용이므로)."""
    ids = {}
    async with db.admin_uow() as s:
        for name in ("a", "b"):
            identity_id, learner_id = uuid.uuid4(), uuid.uuid4()
            await s.execute(
                text("INSERT INTO auth.identity (id, login_kind_cd, login_key, status_cd, created_ts, updated_ts) VALUES (:i, 'EMAIL', :k, 'ACTIVE', now(), now())"),
                {"i": identity_id, "k": f"{name}@test.io"},
            )
            await s.execute(
                text("INSERT INTO account.learner (id, identity_id, nickname, locale_cd, tz_name, status_cd, created_ts, updated_ts) VALUES (:l, :i, :n, 'ko', 'Asia/Seoul', 'ACTIVE', now(), now())"),
                {"l": learner_id, "i": identity_id, "n": name},
            )
            ids[name] = learner_id
    return ids["a"], ids["b"]


async def test_learner_sees_only_own_rows(db):
    a, b = await _seed_two_learners(db)
    async with db.learner_uow(a) as s:
        rows = (await s.execute(text("SELECT id FROM account.learner"))).scalars().all()
    assert rows == [a]  # B 는 존재 자체가 안 보임

    async with db.learner_uow(a) as s:
        row = (await s.execute(
            text("SELECT id FROM account.learner WHERE id = :b"), {"b": b}
        )).first()
    assert row is None  # id 를 알아도 못 본다


async def test_no_context_sees_nothing_fail_closed(db):
    await _seed_two_learners(db)
    async with db._app_sessions() as s:  # GUC 주입 없는 raw 앱 세션
        rows = (await s.execute(text("SELECT id FROM account.learner"))).scalars().all()
    assert rows == []  # 정책의 current_setting 이 NULL → 0행


async def test_app_dsn_cannot_reach_auth_schema(db):
    await _seed_two_learners(db)
    a = uuid.uuid4()
    async with db.learner_uow(a) as s:
        with pytest.raises(Exception) as exc:
            await s.execute(text("SELECT id FROM auth.identity"))
    assert "permission denied" in str(exc.value).lower()


async def test_app_dsn_cannot_hard_delete(db):
    a, _ = await _seed_two_learners(db)
    async with db.learner_uow(a) as s:
        with pytest.raises(Exception) as exc:
            await s.execute(text("DELETE FROM account.learner WHERE id = :a"), {"a": a})
    assert "permission denied" in str(exc.value).lower()


async def test_admin_bypasses_rls(db):
    await _seed_two_learners(db)
    async with db.admin_uow() as s:
        count = (await s.execute(text("SELECT count(*) FROM account.learner"))).scalar()
    assert count == 2  # carrot/worker 경로는 전체 조회 가능


async def test_cannot_insert_row_for_other_learner(db):
    a, b = await _seed_two_learners(db)
    async with db.learner_uow(a) as s:
        with pytest.raises(Exception) as exc:
            await s.execute(
                text("INSERT INTO account.device (id, learner_id, platform_cd, device_key, created_ts, updated_ts) VALUES (:i, :b, 'IOS', 'dev-key', now(), now())"),
                {"i": uuid.uuid4(), "b": b},  # A 컨텍스트로 B 의 기기 등록 시도
            )
    assert "row-level security" in str(exc.value).lower()


async def test_learner_can_update_own_row(db):
    a, _ = await _seed_two_learners(db)
    async with db.learner_uow(a) as s:
        await s.execute(
            text("UPDATE account.learner SET nickname = 'renamed' WHERE id = :a"), {"a": a}
        )
    async with db.learner_uow(a) as s:
        name = (await s.execute(
            text("SELECT nickname FROM account.learner WHERE id = :a"), {"a": a}
        )).scalar()
    assert name == "renamed"


async def test_learning_pattern_card_owner_isolation(db):
    """learning 스키마도 소유 격리 — ORM 모델 없음(raw SQL 마이그레이션 SSOT)이라 text() INSERT."""
    a, b = await _seed_two_learners(db)
    async with db.admin_uow() as s:  # A 소유 pattern_card 1행 (auth/learner 처럼 admin 경로로 seed)
        await s.execute(
            text(
                "INSERT INTO learning.pattern_card "
                "(id, learner_id, pattern_key, status_cd, fsrs_due_ts, fsrs_stability, fsrs_card, recall_window_ms, created_ts, updated_ts) "
                "VALUES (:id, :a, 'used-to', 'ACTIVE', now(), 1.0, '{}'::jsonb, 12000, now(), now())"
            ),
            {"id": uuid.uuid4(), "a": a},
        )
    async with db.learner_uow(a) as s:  # 주인은 본다
        rows = (await s.execute(text("SELECT id FROM learning.pattern_card"))).scalars().all()
    assert len(rows) == 1
    async with db.learner_uow(b) as s:  # 남은 못 본다
        rows = (await s.execute(text("SELECT id FROM learning.pattern_card"))).scalars().all()
    assert rows == []
    # 위 learner_uow 들이 쓴 커넥션이 풀에서 GUC=''(빈문자열)로 복귀 → 그 dirty 커넥션에서도
    # 에러가 아니라 0행이어야 한다 (rls _LEARNER_GUC 의 NULLIF 가드 회귀 방지).
    async with db._app_sessions() as s:  # GUC 없음 → fail-closed
        rows = (await s.execute(text("SELECT id FROM learning.pattern_card"))).scalars().all()
    assert rows == []


async def test_call_ring_owner_isolation(db):
    """call 스키마 소유 격리 — 마찬가지로 raw SQL INSERT."""
    a, b = await _seed_two_learners(db)
    async with db.admin_uow() as s:
        await s.execute(
            text(
                "INSERT INTO call.ring (id, learner_id, status_cd, scheduled_ts, created_ts, updated_ts) "
                "VALUES (:id, :a, 'SCHEDULED', now(), now(), now())"
            ),
            {"id": uuid.uuid4(), "a": a},
        )
    async with db.learner_uow(a) as s:
        assert len((await s.execute(text("SELECT id FROM call.ring"))).scalars().all()) == 1
    async with db.learner_uow(b) as s:
        assert (await s.execute(text("SELECT id FROM call.ring"))).scalars().all() == []
