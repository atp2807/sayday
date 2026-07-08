"""OpsRepo 구현 — ops 스키마 전용 (raw SQL, append-only, admin uow 만 도달).

자기 스키마만 접근한다 (ops.*). RLS 로 잠겨(rls._LOCKED_TABLES) app 롤은 스키마 USAGE 도
없어 도달 불가 — worker/carrot 의 admin(BYPASSRLS) uow 경로만 기록·조회한다(§3).
detail 은 dict → jsonb (billing_repo.py 의 CAST(:x AS jsonb) 스타일). 갱신/삭제 없음.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.records import OpLogRecord, StateLogRecord

_OP_COLS = "id, actor_cd, action_cd, detail, created_ts"
_STATE_COLS = "id, entity_cd, entity_id, from_cd, to_cd, created_ts"


def _to_op(m: RowMapping) -> OpLogRecord:
    return OpLogRecord(
        id=m["id"],
        actor_cd=m["actor_cd"],
        action_cd=m["action_cd"],
        detail=m["detail"],
        created_ts=m["created_ts"],
    )


def _to_state(m: RowMapping) -> StateLogRecord:
    return StateLogRecord(
        id=m["id"],
        entity_cd=m["entity_cd"],
        entity_id=m["entity_id"],
        from_cd=m["from_cd"],
        to_cd=m["to_cd"],
        created_ts=m["created_ts"],
    )


class SqlOpsRepo:
    """ops 스키마 repo — AsyncSession 주입 (admin uow 만 도달 가능)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def log_op(self, actor_cd: str, action_cd: str, detail: dict[str, Any] | None) -> None:
        d = json.dumps(detail) if detail is not None else None
        await self._s.execute(
            sa_text(
                "INSERT INTO ops.op_log (actor_cd, action_cd, detail) "
                "VALUES (:a, :ac, CAST(:d AS jsonb))"
            ),
            {"a": actor_cd, "ac": action_cd, "d": d},
        )

    async def log_audit(
        self,
        actor_cd: str,
        subject_id: UUID | None,
        change_cd: str,
        detail: dict[str, Any] | None,
    ) -> None:
        d = json.dumps(detail) if detail is not None else None
        await self._s.execute(
            sa_text(
                "INSERT INTO ops.audit_log (actor_cd, subject_id, change_cd, detail) "
                "VALUES (:a, :sid, :ch, CAST(:d AS jsonb))"
            ),
            {"a": actor_cd, "sid": subject_id, "ch": change_cd, "d": d},
        )

    async def log_state(
        self, entity_cd: str, entity_id: UUID, from_cd: str | None, to_cd: str
    ) -> None:
        await self._s.execute(
            sa_text(
                "INSERT INTO ops.state_log (entity_cd, entity_id, from_cd, to_cd) "
                "VALUES (:e, :eid, :fr, :to)"
            ),
            {"e": entity_cd, "eid": entity_id, "fr": from_cd, "to": to_cd},
        )

    async def recent_state_logs(self, limit: int = 50) -> list[StateLogRecord]:
        rows = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_STATE_COLS} FROM ops.state_log "
                    "ORDER BY created_ts DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).mappings().all()
        return [_to_state(m) for m in rows]

    async def recent_op_logs(self, limit: int = 50) -> list[OpLogRecord]:
        rows = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_OP_COLS} FROM ops.op_log "
                    "ORDER BY created_ts DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).mappings().all()
        return [_to_op(m) for m in rows]
