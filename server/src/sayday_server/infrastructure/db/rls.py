"""RLS 정책 SSOT — fail-closed (링크로어 패턴, ARCHITECTURE §2 레이어2).

원칙:
- 모든 테이블 ENABLE + FORCE ROW LEVEL SECURITY.
- auth 스키마: app 롤에 GRANT 자체가 없다 → 앱 DSN 에서 자격증명 도달 불가.
- account 스키마: app 롤에 SELECT/INSERT/UPDATE 만 (DELETE 없음 = 하드삭제 금지를 DB가 강제).
- 정책은 GUC `app.current_learner_id` 기준. GUC 미설정/빈문자열 시 NULL → 0행 (fail-closed).
- admin DSN(BYPASSRLS)은 정책 우회 — carrot/worker/마이그레이션 전용.

NULLIF 이유: set_config(...,true)(SET LOCAL) 로 GUC 를 쓴 커넥션이 풀에 반납되면 트랜잭션 종료 시
값이 NULL 이 아니라 ''(빈문자열) 로 복귀한다. ''::uuid 는 예외를 던지므로(0행이 아니라 에러 = fail-closed 훼손),
NULLIF 로 ''→NULL 처리해 어떤 경우에도 깨끗이 0행이 되게 한다.
"""
from __future__ import annotations

APP_ROLE = "sayday_app"

_LEARNER_GUC = "NULLIF(current_setting('app.current_learner_id', true), '')::uuid"

# app 롤에 USAGE 를 여는 스키마 (auth 는 제외 = 도달 불가)
_APP_SCHEMAS: tuple[str, ...] = ("account", "learning", "call")

# (schema, table, 소유 판별 SQL) — 새 learner 소유 테이블은 여기 추가 (schema-guard 대상)
_OWNED_TABLES: list[tuple[str, str, str]] = [
    ("account", "learner", f"id = {_LEARNER_GUC}"),
    ("account", "device", f"learner_id = {_LEARNER_GUC}"),
    ("account", "push_token", f"learner_id = {_LEARNER_GUC}"),
    ("account", "consent", f"learner_id = {_LEARNER_GUC}"),
    # learning 스키마 (raw SQL 마이그레이션이 SSOT — ORM 모델 없음)
    ("learning", "pattern_card", f"learner_id = {_LEARNER_GUC}"),
    ("learning", "recall_entry", f"learner_id = {_LEARNER_GUC}"),
    # call 스키마 (raw SQL 마이그레이션이 SSOT — ORM 모델 없음)
    ("call", "ring_slot", f"learner_id = {_LEARNER_GUC}"),
    ("call", "ring", f"learner_id = {_LEARNER_GUC}"),
    ("call", "utterance", f"learner_id = {_LEARNER_GUC}"),
    ("call", "correction", f"learner_id = {_LEARNER_GUC}"),
    ("call", "ring_report", f"learner_id = {_LEARNER_GUC}"),
]

# app 롤 정책 없이 잠그는 테이블 (RLS 만 켬 → app 접근 시 0행/거부)
_LOCKED_TABLES: list[tuple[str, str]] = [
    ("auth", "identity"),
    ("auth", "credential"),
    ("auth", "refresh_token"),
    ("auth", "otp"),
]


def rls_ddl() -> list[str]:
    """스키마 생성 후 실행할 RLS/GRANT 문 전체 (순서 보장)."""
    stmts: list[str] = []

    # 스키마 권한: account/learning/call 을 app 에 개방, auth 는 GRANT 없음 (도달 불가)
    for schema in _APP_SCHEMAS:
        stmts.append(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}")

    for schema, table in _LOCKED_TABLES:
        stmts += [
            f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY",
        ]

    for schema, table, predicate in _OWNED_TABLES:
        stmts += [
            f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY",
            # 하드삭제 금지: DELETE 권한 자체를 주지 않는다 (원칙 6)
            f"GRANT SELECT, INSERT, UPDATE ON {schema}.{table} TO {APP_ROLE}",
            # 멱등화: CREATE POLICY 는 비멱등이라 재실행 시 실패 → 먼저 DROP IF EXISTS
            f"DROP POLICY IF EXISTS {table}_owner ON {schema}.{table}",
            f"""
            CREATE POLICY {table}_owner ON {schema}.{table}
            FOR ALL TO {APP_ROLE}
            USING ({predicate})
            WITH CHECK ({predicate})
            """,
        ]
    return [s.strip() for s in stmts]
