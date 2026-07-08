"""schedule_svc — ring_slot 생성 + 발신 대상 슬롯 조회 (오케스트레이션).

서비스 = 모듈레벨 async def. application 은 infrastructure 를 import 하지 않는다
(원칙 5) — 포트(UowFactory)를 인자로 주입받는다. now 는 인자로 명시 전달(순수·결정적).
_compute_next_fire 는 zoneinfo 만 쓰는 순수 함수로 분리해 단독 테스트한다.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from .records import RingSlotRecord
from .repos import UowFactory


def _compute_next_fire(
    days_of_week: int, local_time: time, tz_name: str, now: datetime
) -> datetime:
    """days_of_week 비트마스크(월=1<<0 .. 일=1<<6) 중 now 이후 가장 가까운
    local_time 발생시각을 tz 적용해 UTC 로 환산.

    - 오늘이 대상 요일이고 local_time 이 아직 안 지났으면 오늘.
    - 아니면 앞으로 7일 내 첫 대상 요일. (요일 비트가 하나도 없으면 ValueError.)
    """
    tz = ZoneInfo(tz_name)
    now_local = now.astimezone(tz)
    for offset in range(8):  # 오늘(0) .. +7일 — 주간 순환이라 8회면 반드시 걸린다
        cand_date = (now_local + timedelta(days=offset)).date()
        if not (days_of_week & (1 << cand_date.weekday())):  # weekday(): 월=0..일=6
            continue
        cand = datetime.combine(cand_date, local_time, tzinfo=tz)
        if cand > now_local:
            return cand.astimezone(UTC)
    raise ValueError(f"활성 요일 없음: days_of_week={days_of_week:#09b}")


async def create_ring_slot(
    uowf: UowFactory,
    learner_id: UUID,
    days_of_week: int,
    local_time: time,
    tz_name: str,
    now: datetime,
) -> RingSlotRecord:
    """반복 발신 슬롯 생성 — next_fire_ts 를 계산해 learner uow 로 저장."""
    next_fire = _compute_next_fire(days_of_week, local_time, tz_name, now)
    async with uowf.learner(learner_id) as uow:
        return await uow.call.create_ring_slot(
            learner_id, days_of_week, local_time, tz_name, next_fire
        )


async def list_due_slots(uowf: UowFactory, now: datetime) -> list[RingSlotRecord]:
    """발신 대상(active + next_fire<=now) 슬롯 — worker(admin) 경로."""
    async with uowf.admin() as uow:
        return await uow.call.list_active_slots_due(now)
