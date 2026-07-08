"""subscription 상태머신 — 순수 전이표 (IO 0, stdlib enum 외 import 0).

ring_state.py 를 그대로 본떴다. 전이 규칙'만' 안다. 규칙 위반을 API 에러(409)로
바꾸는 것은 서비스의 몫이다 — domain 은 application 을 import 하지 않는다(원칙 5).
따라서 서비스는 can_transition 으로 가드하고 application.errors.InvalidStateError 를
던진다. assert_transition 은 순수 도메인/유닛테스트용이며, 위반 시 도메인 예외
(IllegalTransition)를 던진다. (ARCHITECTURE §3: TRIAL→ACTIVE→PAST_DUE→CANCELLED/EXPIRED)
"""
from __future__ import annotations

from enum import Enum


class SubscriptionStatus(str, Enum):
    """구독 1건의 수명주기 상태 (billing.subscription.status_cd)."""

    TRIAL = "TRIAL"          # 결제 세션 발급 후 첫 결제 대기 (무료 체험)
    ACTIVE = "ACTIVE"        # 결제 성공 — 유효 기간 내
    PAST_DUE = "PAST_DUE"    # 갱신 결제 실패 — 유예 (재시도 대기)
    CANCELLED = "CANCELLED"  # 사용자/관리자 해지 (종결)
    EXPIRED = "EXPIRED"      # 기간 만료 후 미갱신 (종결)


# 전이표 — 값은 도달 가능한 다음 상태. 빈 집합 = 종결(terminal).
_TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    SubscriptionStatus.TRIAL: frozenset(
        {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED}
    ),
    SubscriptionStatus.ACTIVE: frozenset(
        {SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED}
    ),
    SubscriptionStatus.PAST_DUE: frozenset(
        {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED}
    ),
    SubscriptionStatus.CANCELLED: frozenset(),
    SubscriptionStatus.EXPIRED: frozenset(),
}

TERMINAL: frozenset[SubscriptionStatus] = frozenset(
    status for status, nxt in _TRANSITIONS.items() if not nxt
)


class IllegalTransition(ValueError):
    """도메인 전이 규칙 위반 — 서비스가 InvalidStateError 로 번역한다."""


def _coerce(status: SubscriptionStatus | str) -> SubscriptionStatus:
    return status if isinstance(status, SubscriptionStatus) else SubscriptionStatus(status)


def can_transition(frm: SubscriptionStatus | str, to: SubscriptionStatus | str) -> bool:
    """frm→to 전이가 허용되는가. 미지 상태값이면 False (조용히 거절)."""
    try:
        f, t = _coerce(frm), _coerce(to)
    except ValueError:
        return False
    return t in _TRANSITIONS.get(f, frozenset())


def assert_transition(frm: SubscriptionStatus | str, to: SubscriptionStatus | str) -> None:
    """위반 시 IllegalTransition. (서비스는 can_transition 을 쓰고, 이 함수는 순수용.)"""
    if not can_transition(frm, to):
        raise IllegalTransition(f"illegal subscription transition: {frm} -> {to}")


def is_terminal(status: SubscriptionStatus | str) -> bool:
    return _coerce(status) in TERMINAL
