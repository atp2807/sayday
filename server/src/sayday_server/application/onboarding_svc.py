"""onboarding_svc — B1: 레벨 설정 + 초기 문형 카드 배정 (온보딩 파이프라인).

learner 본인이 진행하는 경로라 learner uow(RLS 적용)를 쓴다. 서비스 = 모듈레벨
async def, application 은 infrastructure 를 import 하지 않는다(원칙 5) —
UowFactory·CatalogPort 를 주입받는다. 멱등: 이미 있는 카드는 건드리지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..domain import recall_calc
from .repos import CatalogPort, UowFactory


async def complete_onboarding(
    uowf: UowFactory,
    catalog: CatalogPort,
    learner_id: UUID,
    level_cd: str,
    now: datetime,
    count: int = 4,
) -> list[str]:
    """온보딩 완료: 레벨 저장 + 초기 문형 카드 배정 (신규만 생성, 재호출해도 안전)."""
    async with uowf.learner(learner_id) as uow:
        await uow.account.set_learner_level(learner_id, level_cd)

        keys = await catalog.starter_pool(level_cd, count)

        assigned: list[str] = []
        for key in keys:
            existing = await uow.learning.get_card(learner_id, key)
            if existing is None:
                await uow.learning.save_card(learner_id, recall_calc.new_pattern_card(key, now))
            assigned.append(key)

        return assigned
