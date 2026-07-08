"""손수 쓴 raw SQL 마이그레이션 러너 — Alembic 미채택(lr-e2b705eb, 데이터 유실 경험).

원칙:
- 마이그레이션 = migrations/*.sql (구조 SSOT: 스키마/테이블/인덱스/트리거/FK). autogenerate 금지.
- 추적 = public.migration_history (filename PK + checksum). 모든 적용 경로가 이 표를 갱신.
- 불변성 가드: 이미 적용된 파일의 내용이 바뀌면(checksum 불일치) RuntimeError — 편집 대신 새 파일.
- 멱등: 각 .sql 은 IF NOT EXISTS 등으로 재실행 안전. 러너도 적용분은 skip.
- rls.rls_ddl() 이 정책/권한 SSOT — 여기서 구조 적용 후 재적용(멱등).
- 백업: test 아닌 환경은 pg_dump 백업이 선행(lr-b087b1a5). pg_dump 없으면 거부.

다중문장 실행: asyncpg 확장 프로토콜은 다중문장을 지원하지 않는다(트리거/함수/DO$$ 포함).
raw asyncpg 연결의 simple 프로토콜(execute, 인자 없을 때)로 실행한다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ...config import Settings
from .engine import Db
from .rls import rls_ddl

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def apply_sql_migrations(conn: AsyncConnection) -> list[str]:
    """migrations/*.sql 을 파일명 순서대로 멱등 적용하고 migration_history 로 추적한다.

    전달받은 conn 의 트랜잭션 안에서 동작한다(자체 begin 하지 않음).
    반환값: 이번 호출에서 실제로 새로 적용된 filename 리스트(재실행 시 []).
    """
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS public.migration_history ("
            "filename text PRIMARY KEY, "
            "checksum text NOT NULL, "
            "applied_ts timestamptz NOT NULL DEFAULT now())"
        )
    )

    prior_rows = (
        await conn.execute(text("SELECT filename, checksum FROM public.migration_history"))
    ).all()
    applied: dict[str, str] = {row[0]: row[1] for row in prior_rows}

    files = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
    newly_applied: list[str] = []

    for path in files:
        script = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
        recorded = applied.get(path.name)
        if recorded is not None:
            if recorded != checksum:
                raise RuntimeError(
                    f"마이그레이션 불변성 위반: {path.name} 은(는) 이미 적용됐으나 내용이 변경됨 "
                    f"(기록 {recorded[:12]}… ≠ 현재 {checksum[:12]}…). "
                    "적용된 마이그레이션은 편집 금지 — 새 파일로 추가하라."
                )
            continue  # 동일 checksum → 이미 적용됨, skip

        # 다중문장 DDL: raw asyncpg 연결의 simple 프로토콜로 (확장 프로토콜은 다중문장 미지원)
        raw = await conn.get_raw_connection()
        driver_conn = raw.driver_connection  # 실 asyncpg.Connection (Optional[Any])
        if driver_conn is None:  # pragma: no cover - 열린 연결에선 발생 안 함
            raise RuntimeError(f"{path.name}: raw asyncpg 연결 획득 실패")
        await driver_conn.execute(script)

        await conn.execute(
            text(
                "INSERT INTO public.migration_history (filename, checksum) VALUES (:f, :c)"
            ),
            {"f": path.name, "c": checksum},
        )
        newly_applied.append(path.name)

    return newly_applied


def _libpq_dsn(async_dsn: str) -> str:
    """SQLAlchemy asyncpg DSN → libpq(pg_dump) 접속 URI (postgresql+asyncpg:// → postgresql://)."""
    return async_dsn.replace("+asyncpg", "", 1)


def _run_pg_dump(admin_dsn: str) -> Path:
    """pg_dump 로 전체 백업. pg_dump 없으면 RuntimeError(백업 없이 프로덕션 마이그레이션 금지)."""
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise RuntimeError(
            "pg_dump 없음 — 백업 없이 마이그레이션 불가. "
            "PostgreSQL client(pg_dump) 설치 후 재시도하거나, 백업이 정말 불필요하면 "
            "migrate(cfg, backup=False) 로 명시적으로 우회하라."
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(os.environ.get("SAYDAY_BACKUP_DIR", "."))
    out = backup_dir / f"sayday_backup_{stamp}.sql"
    subprocess.run(  # noqa: S603 - 신뢰된 관리자 CLI 경로
        [pg_dump, f"--file={out}", _libpq_dsn(admin_dsn)],
        check=True,
    )
    return out


async def migrate(cfg: Settings, *, backup: bool | None = None) -> list[str]:
    """운영 마이그레이션 진입점: (백업) → SQL 구조 적용 → RLS/GRANT 재적용(멱등).

    backup 기본값: test 환경이 아니면 True (프로덕션은 백업 없이 금지).
    반환값: 이번 호출에서 새로 적용된 filename 리스트.
    """
    if backup is None:
        backup = cfg.env != "test"

    if backup:
        _run_pg_dump(cfg.db_dsn_admin)

    db = Db(cfg)
    try:
        async with db.admin_engine.begin() as conn:
            newly_applied = await apply_sql_migrations(conn)
            for stmt in rls_ddl():
                await conn.execute(text(stmt))
    finally:
        await db.dispose()
    return newly_applied


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from ...config import settings

    asyncio.run(migrate(settings()))
