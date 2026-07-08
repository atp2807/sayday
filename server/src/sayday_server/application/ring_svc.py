"""ring_svc — 발신 시작(start_ring) + 상태 전이(transition_ring).

서비스 = 모듈레벨 async def. application 은 infrastructure 를 import 하지 않는다
(원칙 5) — UowFactory·gateway 포트·CatalogPort 를 인자로 주입받는다. now/rng 도 인자
주입(결정적). start_ring 은 worker(admin) 경로라 admin uow 를 쓴다.

상태 전이 가드는 domain.ring_state.can_transition 으로 하고, 위반은 여기서
InvalidStateError(409)로 던진다 — domain 은 application 을 import 할 수 없으므로
전이 규칙(domain)과 에러 번역(application)의 경계가 여기다.
"""
from __future__ import annotations

import random
from datetime import datetime
from uuid import UUID

from ..domain import ring_state
from ..domain.drill_calc import plan_ring
from ..domain.ring_state import RingStatus
from ..domain.verdict_calc import should_force
from .errors import InvalidStateError, NotFoundError
from .ports import PushPort, RingPort
from .records import RingRecord
from .repos import CatalogPort, UowFactory

# 통화 중 종료로 간주해 타임스탬프를 기록하는 상태
_STARTS_CALL: frozenset[RingStatus] = frozenset({RingStatus.IN_CALL})
_ENDS_CALL: frozenset[RingStatus] = frozenset({RingStatus.ENDED, RingStatus.DROPPED})


def _status_value(status: RingStatus | str) -> str:
    return status.value if isinstance(status, RingStatus) else status


def _guard(cur: str, to: str) -> None:
    if not ring_state.can_transition(cur, to):
        raise InvalidStateError(f"ring 전이 불가: {cur} -> {to}")


async def start_ring(
    uowf: UowFactory,
    ring_port: RingPort,
    push: PushPort,
    catalog: CatalogPort,
    learner_id: UUID,
    slot_id: UUID | None,
    now: datetime,
    rng: random.Random,
) -> RingRecord:
    """예약 슬롯 발신 개시 — drill_plan 확정 → ring 생성 → 방 grant → RINGING.

    worker(admin) 경로. 한 트랜잭션 안에서 ring 생성·상태전이가 함께 커밋된다.
    push_token 은 이 단계 범위 밖(앱 push 는 E5) — 최소 호출만 시연한다.
    """
    async with uowf.admin() as uow:
        cards = await uow.learning.list_cards(learner_id)
        forced_keys: list[str] = []
        for card in cards:
            recents = await uow.learning.recent_recalls(learner_id, card.pattern_key)
            if should_force(recents):
                forced_keys.append(card.pattern_key)
        new_pool = await catalog.new_pool([c.pattern_key for c in cards])

        plan = plan_ring(cards, forced_keys, new_pool, now, rng)

        ring = await uow.call.create_ring(
            learner_id,
            slot_id,
            plan,
            None,
            now,
            RingStatus.SCHEDULED.value,
        )

        grant = await ring_port.mint_room_grant(ring.id, learner_id)

        _guard(ring.status_cd, RingStatus.RINGING.value)
        await uow.call.set_ring_status(
            ring.id, RingStatus.RINGING.value, room_grant_ref=grant.token
        )

        # push_token 은 아직 없음(앱 E5) — 빈 토큰으로 발신 트리거만 시연.
        await push.send_ring_push("", "RING", ring.id)

        started = await uow.call.get_ring(ring.id)
        assert started is not None  # 방금 만든 ring
        return started


async def transition_ring(
    uowf: UowFactory,
    ring_id: UUID,
    to_status: RingStatus | str,
    now: datetime,
) -> RingRecord:
    """ring 상태 전이 — 규칙 검증 후 적절한 started_ts/ended_ts 기록.

    worker(admin) 경로. 없는 ring 은 NotFoundError, 불가 전이는 InvalidStateError.
    """
    to_value = _status_value(to_status)
    async with uowf.admin() as uow:
        ring = await uow.call.get_ring(ring_id)
        if ring is None:
            raise NotFoundError(f"ring 없음: {ring_id}", error_code="RING_002")

        _guard(ring.status_cd, to_value)

        to_enum = RingStatus(to_value)
        started_ts = now if to_enum in _STARTS_CALL else None
        ended_ts = now if to_enum in _ENDS_CALL else None
        await uow.call.set_ring_status(
            ring_id, to_value, started_ts=started_ts, ended_ts=ended_ts
        )

        updated = await uow.call.get_ring(ring_id)
        assert updated is not None
        return updated
