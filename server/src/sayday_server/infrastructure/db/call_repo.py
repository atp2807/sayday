"""CallRepo 구현 — call 스키마 전용 (raw SQL, ORM 모델 없음: E3.5 결정).

자기 스키마만 접근한다 (call.*). drill_plan 은 ElicitStep 튜플을 jsonb 로
직렬화/역직렬화한다. sqlalchemy.text 는 add_utterance 의 `text` 파라미터(스펙 계약)와
이름이 겹쳐 sa_text 로 별칭한다.
"""
from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.records import (
    RingRecord,
    RingReportRecord,
    RingSlotRecord,
    UtteranceRecord,
)
from ...domain.pattern import DrillPlan, ElicitStep, StepKind

_SLOT_COLS = "id, learner_id, days_of_week, local_time, tz_name, active_yn, next_fire_ts"
_RING_COLS = (
    "id, learner_id, ring_slot_id, status_cd, drill_plan, room_grant_ref, "
    "scheduled_ts, started_ts, ended_ts"
)
_UTT_COLS = (
    "id, ring_id, learner_id, seq, speaker_cd, source_cd, text, "
    "target_pattern_key, verdict_cd, response_ms"
)
_REPORT_COLS = "id, ring_id, learner_id, summary, metrics"


def _drill_plan_to_json(plan: DrillPlan) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "pattern_key": s.pattern_key,
                    "kind": s.kind.value,
                    "recall_window_ms": s.recall_window_ms,
                }
                for s in plan.steps
            ]
        }
    )


def _drill_plan_from_json(raw: Any) -> DrillPlan:
    return DrillPlan(
        steps=tuple(
            ElicitStep(
                pattern_key=step["pattern_key"],
                kind=StepKind(step["kind"]),
                recall_window_ms=step["recall_window_ms"],
            )
            for step in raw["steps"]
        )
    )


def _to_slot(m: RowMapping) -> RingSlotRecord:
    return RingSlotRecord(
        id=m["id"],
        learner_id=m["learner_id"],
        days_of_week=m["days_of_week"],
        local_time=m["local_time"],
        tz_name=m["tz_name"],
        active_yn=m["active_yn"],
        next_fire_ts=m["next_fire_ts"],
    )


def _to_ring(m: RowMapping) -> RingRecord:
    raw = m["drill_plan"]
    return RingRecord(
        id=m["id"],
        learner_id=m["learner_id"],
        ring_slot_id=m["ring_slot_id"],
        status_cd=m["status_cd"],
        drill_plan=_drill_plan_from_json(raw) if raw is not None else None,
        room_grant_ref=m["room_grant_ref"],
        scheduled_ts=m["scheduled_ts"],
        started_ts=m["started_ts"],
        ended_ts=m["ended_ts"],
    )


def _to_utterance(m: RowMapping) -> UtteranceRecord:
    return UtteranceRecord(
        id=m["id"],
        ring_id=m["ring_id"],
        learner_id=m["learner_id"],
        seq=m["seq"],
        speaker_cd=m["speaker_cd"],
        source_cd=m["source_cd"],
        text=m["text"],
        target_pattern_key=m["target_pattern_key"],
        verdict_cd=m["verdict_cd"],
        response_ms=m["response_ms"],
    )


def _to_report(m: RowMapping) -> RingReportRecord:
    return RingReportRecord(
        id=m["id"],
        ring_id=m["ring_id"],
        learner_id=m["learner_id"],
        summary=m["summary"],
        metrics=m["metrics"],
    )


class SqlCallRepo:
    """call 스키마 repo — AsyncSession 주입 (UoW 가 공유 세션 전달)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_ring_slot(
        self,
        learner_id: UUID,
        days_of_week: int,
        local_time: time,
        tz_name: str,
        next_fire_ts: datetime | None,
    ) -> RingSlotRecord:
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO call.ring_slot "
                    "(learner_id, days_of_week, local_time, tz_name, next_fire_ts) "
                    "VALUES (:lid, :dow, :lt, :tz, CAST(:nf AS timestamptz)) "
                    f"RETURNING {_SLOT_COLS}"
                ),
                {
                    "lid": learner_id,
                    "dow": days_of_week,
                    "lt": local_time,
                    "tz": tz_name,
                    "nf": next_fire_ts,
                },
            )
        ).mappings().one()
        return _to_slot(m)

    async def list_active_slots_due(self, now: datetime) -> list[RingSlotRecord]:
        rows = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_SLOT_COLS} FROM call.ring_slot "
                    "WHERE active_yn AND next_fire_ts <= :now"
                ),
                {"now": now},
            )
        ).mappings().all()
        return [_to_slot(m) for m in rows]

    async def set_slot_next_fire(self, slot_id: UUID, next_fire_ts: datetime | None) -> None:
        await self._s.execute(
            sa_text(
                "UPDATE call.ring_slot SET next_fire_ts = CAST(:nf AS timestamptz) "
                "WHERE id = :sid"
            ),
            {"nf": next_fire_ts, "sid": slot_id},
        )

    async def create_ring(
        self,
        learner_id: UUID,
        ring_slot_id: UUID | None,
        drill_plan: DrillPlan | None,
        room_grant_ref: str | None,
        scheduled_ts: datetime,
        status_cd: str,
    ) -> RingRecord:
        dp = _drill_plan_to_json(drill_plan) if drill_plan is not None else None
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO call.ring "
                    "(learner_id, ring_slot_id, status_cd, drill_plan, "
                    "room_grant_ref, scheduled_ts) "
                    "VALUES (:lid, :sid, :st, CAST(:dp AS jsonb), :rgr, :sch) "
                    f"RETURNING {_RING_COLS}"
                ),
                {
                    "lid": learner_id,
                    "sid": ring_slot_id,
                    "st": status_cd,
                    "dp": dp,
                    "rgr": room_grant_ref,
                    "sch": scheduled_ts,
                },
            )
        ).mappings().one()
        return _to_ring(m)

    async def get_ring(self, ring_id: UUID) -> RingRecord | None:
        m = (
            await self._s.execute(
                sa_text(f"SELECT {_RING_COLS} FROM call.ring WHERE id = :rid"),
                {"rid": ring_id},
            )
        ).mappings().first()
        return _to_ring(m) if m is not None else None

    async def set_ring_status(
        self,
        ring_id: UUID,
        status_cd: str,
        *,
        started_ts: datetime | None = None,
        ended_ts: datetime | None = None,
    ) -> None:
        # None 인 인자는 기존 값 유지 (COALESCE) — 상태만 바꾸는 호출을 지원.
        await self._s.execute(
            sa_text(
                "UPDATE call.ring SET status_cd = :st, "
                "started_ts = COALESCE(CAST(:started AS timestamptz), started_ts), "
                "ended_ts = COALESCE(CAST(:ended AS timestamptz), ended_ts) "
                "WHERE id = :rid"
            ),
            {"st": status_cd, "started": started_ts, "ended": ended_ts, "rid": ring_id},
        )

    async def add_utterance(
        self,
        ring_id: UUID,
        learner_id: UUID,
        seq: int,
        speaker_cd: str,
        source_cd: str,
        text: str,
        target_pattern_key: str | None,
        response_ms: int | None,
    ) -> UtteranceRecord:
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO call.utterance "
                    "(ring_id, learner_id, seq, speaker_cd, source_cd, text, "
                    "target_pattern_key, response_ms) "
                    "VALUES (:rid, :lid, :seq, :sp, :src, :txt, :tpk, :rms) "
                    f"RETURNING {_UTT_COLS}"
                ),
                {
                    "rid": ring_id,
                    "lid": learner_id,
                    "seq": seq,
                    "sp": speaker_cd,
                    "src": source_cd,
                    "txt": text,
                    "tpk": target_pattern_key,
                    "rms": response_ms,
                },
            )
        ).mappings().one()
        return _to_utterance(m)

    async def list_utterances(self, ring_id: UUID) -> list[UtteranceRecord]:
        rows = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_UTT_COLS} FROM call.utterance "
                    "WHERE ring_id = :rid ORDER BY seq"
                ),
                {"rid": ring_id},
            )
        ).mappings().all()
        return [_to_utterance(m) for m in rows]

    async def set_utterance_verdict(self, utterance_id: UUID, verdict_cd: str) -> None:
        await self._s.execute(
            sa_text("UPDATE call.utterance SET verdict_cd = :vc WHERE id = :uid"),
            {"vc": verdict_cd, "uid": utterance_id},
        )

    async def add_correction(
        self,
        ring_id: UUID,
        learner_id: UUID,
        utterance_id: UUID | None,
        severity_cd: str,
        original_text: str,
        corrected_text: str,
        note: str | None,
    ) -> None:
        await self._s.execute(
            sa_text(
                "INSERT INTO call.correction "
                "(ring_id, learner_id, utterance_id, severity_cd, "
                "original_text, corrected_text, note) "
                "VALUES (:rid, :lid, :uid, :sev, :orig, :corr, :note)"
            ),
            {
                "rid": ring_id,
                "lid": learner_id,
                "uid": utterance_id,
                "sev": severity_cd,
                "orig": original_text,
                "corr": corrected_text,
                "note": note,
            },
        )

    async def create_ring_report(
        self, ring_id: UUID, learner_id: UUID, summary: str, metrics: dict[str, Any] | None
    ) -> RingReportRecord:
        met = json.dumps(metrics) if metrics is not None else None
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO call.ring_report (ring_id, learner_id, summary, metrics) "
                    "VALUES (:rid, :lid, :sum, CAST(:met AS jsonb)) "
                    f"RETURNING {_REPORT_COLS}"
                ),
                {"rid": ring_id, "lid": learner_id, "sum": summary, "met": met},
            )
        ).mappings().one()
        return _to_report(m)

    async def get_ring_report(self, ring_id: UUID) -> RingReportRecord | None:
        m = (
            await self._s.execute(
                sa_text(f"SELECT {_REPORT_COLS} FROM call.ring_report WHERE ring_id = :rid"),
                {"rid": ring_id},
            )
        ).mappings().first()
        return _to_report(m) if m is not None else None
