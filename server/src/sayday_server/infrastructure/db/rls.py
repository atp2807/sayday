"""RLS 정책 SSOT — fail-closed (링크로어 패턴, ARCHITECTURE §2 레이어2).

원칙:
- 모든 테이블 ENABLE + FORCE ROW LEVEL SECURITY.
- auth 스키마: app 롤에 GRANT 자체가 없다 → 앱 DSN 에서 자격증명 도달 불가.
- account 스키마: app 롤에 SELECT/INSERT/UPDATE 만 (DELETE 없음 = 하드삭제 금지를 DB가 강제).
- 정책은 GUC `app.current_learner_id` 기준. GUC 미설정 시 NULL → 0행 (fail-closed).
- admin DSN(BYPASSRLS)은 정책 우회 — carrot/worker/마이그레이션 전용.
"""
from __future__ import annotations

APP_ROLE = "sayday_app"

_LEARNER_GUC = "current_setting('app.current_learner_id', true)::uuid"

# (schema, table, 소유 판별 SQL) — 새 learner 소유 테이블은 여기 추가 (schema-guard 대상)
_OWNED_TABLES: list[tuple[str, str, str]] = [
    ("account", "learner", f"id = {_LEARNER_GUC}"),
    ("account", "device", f"learner_id = {_LEARNER_GUC}"),
    ("account", "push_token", f"learner_id = {_LEARNER_GUC}"),
    ("account", "consent", f"learner_id = {_LEARNER_GUC}"),
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

    # 스키마 권한: account 만 app 에 개방, auth 는 GRANT 없음 (도달 불가)
    stmts.append(f"GRANT USAGE ON SCHEMA account TO {APP_ROLE}")

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
            f"""
            CREATE POLICY {table}_owner ON {schema}.{table}
            FOR ALL TO {APP_ROLE}
            USING ({predicate})
            WITH CHECK ({predicate})
            """,
        ]
    return [s.strip() for s in stmts]
