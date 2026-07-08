"""영속 레코드 — DB row 의 application 뷰 (frozen dataclass, ARCHITECTURE §1).

repo 가 입출력하는 순수 데이터. infrastructure 를 import 하지 않는다 (원칙 5 —
application → domain 만 향한다). DrillPlan 은 도메인 값객체라 허용.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from uuid import UUID

from ..domain.pattern import DrillPlan


@dataclass(frozen=True)
class RingSlotRecord:
    """call.ring_slot 1행 — 반복 예약 슬롯."""

    id: UUID
    learner_id: UUID
    days_of_week: int
    local_time: time
    tz_name: str
    active_yn: bool
    next_fire_ts: datetime | None


@dataclass(frozen=True)
class RingRecord:
    """call.ring 1행 — 통화 1건. drill_plan 은 jsonb 에서 복원한 도메인 값."""

    id: UUID
    learner_id: UUID
    ring_slot_id: UUID | None
    status_cd: str
    drill_plan: DrillPlan | None
    room_grant_ref: str | None
    scheduled_ts: datetime
    started_ts: datetime | None
    ended_ts: datetime | None


@dataclass(frozen=True)
class UtteranceRecord:
    """call.utterance 1행 — 발화 1개 (오류보존 전사)."""

    id: UUID
    ring_id: UUID
    learner_id: UUID
    seq: int
    speaker_cd: str
    source_cd: str
    text: str
    target_pattern_key: str | None
    verdict_cd: str | None
    response_ms: int | None


@dataclass(frozen=True)
class CorrectionRecord:
    """call.correction 1행 — 교정 리포트 항목."""

    id: UUID
    ring_id: UUID
    learner_id: UUID
    utterance_id: UUID | None
    severity_cd: str
    original_text: str
    corrected_text: str
    note: str | None


@dataclass(frozen=True)
class RingReportRecord:
    """call.ring_report 1행 — 통화 1건의 최종 리포트 (ring 당 1개)."""

    id: UUID
    ring_id: UUID
    learner_id: UUID
    summary: str
    metrics: dict[str, Any] | None
