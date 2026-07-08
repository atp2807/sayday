"""carrot_svc — 운영자 대시보드 overview (§5 /api/carrot).

서비스 = 모듈레벨 async def. application 은 infrastructure 를 import 하지 않는다
(원칙 5) — UowFactory·포트만 주입받는다. carrot(운영) 경로라 admin uow(BYPASSRLS)로
cross-schema 집계를 한다(§3: cross-schema 오케스트레이션은 서비스에서만).
"""
from __future__ import annotations

from dataclasses import dataclass

from .records import StateLogRecord
from .repos import UowFactory


@dataclass(frozen=True)
class OverviewRecord:
    """운영 대시보드 스냅샷 — 학습자 수 + ring 상태 분포 + 최근 상태전이 이력."""

    learner_count: int
    rings_by_status: dict[str, int]
    recent_activity: tuple[StateLogRecord, ...]


async def overview(uowf: UowFactory, limit: int = 20) -> OverviewRecord:
    """대시보드 개요 — account.learner 수 + call.ring 상태분포 + ops.state_log 최근 limit건.

    admin uow (carrot 경로) — RLS 우회로 전수 집계. cross-schema 는 여기(서비스)서만.
    """
    async with uowf.admin() as uow:
        learner_count = await uow.account.count_learners()
        rings_by_status = await uow.call.count_rings_by_status()
        recent = await uow.ops.recent_state_logs(limit)
    return OverviewRecord(
        learner_count=learner_count,
        rings_by_status=rings_by_status,
        recent_activity=tuple(recent),
    )
