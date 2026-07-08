"""billing_svc — 체크아웃 발급·웹훅 처리·구독 자격 확인 (E5 5b).

서비스 = 모듈레벨 async def. application 은 infrastructure 를 import 하지 않는다
(원칙 5) — UowFactory·PayPort 를 인자로 주입받는다. now 도 인자 주입(결정적).
create_checkout/is_entitled 는 learner 경로(RLS, 본인 구독만), handle_payment_event
는 웹훅 시스템경로라 admin uow 를 쓴다. 상태 전이 가드는 domain.subscription_state
.can_transition — 위반이어도 웹훅은 에러를 던지지 않고(PG 는 재시도만 함) 결제 기록만
남긴다(스펙 5b, ring_svc 의 InvalidStateError 패턴과 달리 웹훅 경로는 조용히 스킵).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from ..domain.subscription_state import can_transition
from .errors import NotFoundError
from .ports import CheckoutSession, PayPort, PaymentEvent
from .repos import UowFactory

# period_cd → 갱신 주기(일). 미지 코드는 기본값(30일)으로 처리한다 — 결제는 이미
# PG 에서 성사된 이벤트라 여기서 하드에러로 막으면 유료 결제가 무기한 대기 상태로
# 남는다(더 나쁜 실패 모드). 신규 주기 추가 시 이 표에 등록.
_PERIOD_DAYS: dict[str, int] = {"MONTHLY": 30}
_DEFAULT_PERIOD_DAYS = 30


def _period_end(period_cd: str, now: datetime) -> datetime:
    """period_cd → 다음 결제 만료 시각. 순수(IO 0)."""
    return now + timedelta(days=_PERIOD_DAYS.get(period_cd, _DEFAULT_PERIOD_DAYS))


async def create_checkout(
    uowf: UowFactory,
    pay: PayPort,
    learner_id: UUID,
    plan_key: str,
    now: datetime,
) -> CheckoutSession:
    """요금제 체크아웃 세션 발급 + TRIAL 구독 생성.

    learner 경로(RLS) — 본인 구독만 만든다. 없는/비활성 요금제는 NotFoundError.
    """
    async with uowf.learner(learner_id) as uow:
        plan = await uow.billing.get_plan(plan_key)
        if plan is None or not plan.active_yn:
            raise NotFoundError(f"요금제 없음: {plan_key}", error_code="BILL_001")

        session = await pay.create_checkout(learner_id, plan_key, plan.price_amt)
        await uow.billing.create_subscription(learner_id, plan.id, "TRIAL", session.pg_ref)
        return session


async def handle_payment_event(uowf: UowFactory, event: PaymentEvent, now: datetime) -> None:
    """웹훅 결제 이벤트 반영 — pg_tx_ref 재적용은 멱등(no-op).

    admin 경로(웹훅은 특정 learner 세션이 없는 시스템 콜). PAID→ACTIVE, FAILED→
    PAST_DUE 는 can_transition 이 허용할 때만 전이하고, 그 외 status_cd 는 결제
    기록만 남긴다(스펙 5b).
    """
    async with uowf.admin() as uow:
        if await uow.billing.get_payment_by_pg_tx(event.pg_tx_ref) is not None:
            return  # 이미 처리된 거래 — 멱등

        sub = await uow.billing.get_subscription_by_pg_ref(event.pg_ref)
        if sub is None:
            raise NotFoundError(f"구독 없음(pg_ref): {event.pg_ref}", error_code="BILL_002")

        if event.status_cd == "PAID":
            await uow.billing.add_payment(
                sub.learner_id, sub.id, event.amount_amt, "PAID", event.pg_tx_ref, now
            )
            if can_transition(sub.status_cd, "ACTIVE"):
                plan = await uow.billing.get_plan_by_id(sub.plan_id)
                period_cd = plan.period_cd if plan is not None else "MONTHLY"
                await uow.billing.set_subscription_status(
                    sub.id,
                    "ACTIVE",
                    started_ts=now if sub.started_ts is None else None,
                    current_period_end_ts=_period_end(period_cd, now),
                )
        elif event.status_cd == "FAILED":
            await uow.billing.add_payment(
                sub.learner_id, sub.id, event.amount_amt, "FAILED", event.pg_tx_ref, None
            )
            if can_transition(sub.status_cd, "PAST_DUE"):
                await uow.billing.set_subscription_status(sub.id, "PAST_DUE")
        else:
            # REFUNDED 등 — 결제 기록만 남기고 상태 전이는 하지 않는다(스펙 범위 밖).
            await uow.billing.add_payment(
                sub.learner_id, sub.id, event.amount_amt, event.status_cd, event.pg_tx_ref, None
            )


async def is_entitled(uowf: UowFactory, learner_id: UUID, now: datetime) -> bool:
    """유효 구독(ACTIVE + 기간 내) 여부 — ring_svc.start_ring 게이트 훅(연결은 프레젠테이션)."""
    async with uowf.learner(learner_id) as uow:
        sub = await uow.billing.get_current_subscription(learner_id)
        if sub is None:
            return False
        return sub.status_cd == "ACTIVE" and (
            sub.current_period_end_ts is None or sub.current_period_end_ts > now
        )
