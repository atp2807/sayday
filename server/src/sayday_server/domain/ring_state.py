"""ring 상태머신 — 순수 전이표 (IO 0, stdlib enum 외 import 0).

전이 규칙'만' 안다. 규칙 위반을 API 에러(409)로 바꾸는 것은 서비스의 몫이다 —
domain 은 application 을 import 하지 않는다(원칙 5). 따라서 서비스는 can_transition
으로 가드하고 application.errors.InvalidStateError 를 던진다. assert_transition 은
순수 도메인/유닛테스트용이며, 위반 시 도메인 예외(IllegalTransition)를 던진다.
"""
from __future__ import annotations

from enum import Enum


class RingStatus(str, Enum):
    """통화 1건의 수명주기 상태 (call.ring.status_cd)."""

    SCHEDULED = "SCHEDULED"  # 발신 예약됨 (drill_plan 확정)
    RINGING = "RINGING"      # 발신 중 (앱 벨/푸시)
    IN_CALL = "IN_CALL"      # 통화 성립 (실시간 세션)
    ENDED = "ENDED"          # 통화 정상 종료 (리포트 대기)
    REPORTED = "REPORTED"    # 교정 리포트 생성 완료 (종결)
    MISSED = "MISSED"        # 미수신 (종결)
    DECLINED = "DECLINED"    # 거절 (종결)
    DROPPED = "DROPPED"      # 통화 중 끊김 (종결)


# 전이표 — 값은 도달 가능한 다음 상태. 빈 집합 = 종결(terminal).
_TRANSITIONS: dict[RingStatus, frozenset[RingStatus]] = {
    RingStatus.SCHEDULED: frozenset(
        {RingStatus.RINGING, RingStatus.MISSED, RingStatus.DECLINED}
    ),
    RingStatus.RINGING: frozenset(
        {RingStatus.IN_CALL, RingStatus.MISSED, RingStatus.DECLINED, RingStatus.DROPPED}
    ),
    RingStatus.IN_CALL: frozenset({RingStatus.ENDED, RingStatus.DROPPED}),
    RingStatus.ENDED: frozenset({RingStatus.REPORTED}),
    RingStatus.REPORTED: frozenset(),
    RingStatus.MISSED: frozenset(),
    RingStatus.DECLINED: frozenset(),
    RingStatus.DROPPED: frozenset(),
}

TERMINAL: frozenset[RingStatus] = frozenset(
    status for status, nxt in _TRANSITIONS.items() if not nxt
)


class IllegalTransition(ValueError):
    """도메인 전이 규칙 위반 — 서비스가 InvalidStateError 로 번역한다."""


def _coerce(status: RingStatus | str) -> RingStatus:
    return status if isinstance(status, RingStatus) else RingStatus(status)


def can_transition(frm: RingStatus | str, to: RingStatus | str) -> bool:
    """frm→to 전이가 허용되는가. 미지 상태값이면 False (조용히 거절)."""
    try:
        f, t = _coerce(frm), _coerce(to)
    except ValueError:
        return False
    return t in _TRANSITIONS.get(f, frozenset())


def assert_transition(frm: RingStatus | str, to: RingStatus | str) -> None:
    """위반 시 IllegalTransition. (서비스는 can_transition 을 쓰고, 이 함수는 순수용.)"""
    if not can_transition(frm, to):
        raise IllegalTransition(f"illegal ring transition: {frm} -> {to}")


def is_terminal(status: RingStatus | str) -> bool:
    return _coerce(status) in TERMINAL
