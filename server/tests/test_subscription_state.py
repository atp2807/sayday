"""subscription_state 순수 유닛 — 전이표만 검증 (PG 불필요, IO 0)."""
import pytest

from sayday_server.domain.subscription_state import (
    TERMINAL,
    IllegalTransition,
    SubscriptionStatus,
    assert_transition,
    can_transition,
    is_terminal,
)

S = SubscriptionStatus

# (frm, to) — 허용되는 전이 전부 (ARCHITECTURE §3)
LEGAL = [
    (S.TRIAL, S.ACTIVE),
    (S.TRIAL, S.CANCELLED),
    (S.TRIAL, S.EXPIRED),
    (S.ACTIVE, S.PAST_DUE),
    (S.ACTIVE, S.CANCELLED),
    (S.ACTIVE, S.EXPIRED),
    (S.PAST_DUE, S.ACTIVE),
    (S.PAST_DUE, S.CANCELLED),
    (S.PAST_DUE, S.EXPIRED),
]


@pytest.mark.parametrize("frm,to", LEGAL)
def test_legal_transitions_allowed(frm, to):
    assert can_transition(frm, to) is True
    assert_transition(frm, to)  # raise 안 함


def test_illegal_transitions_rejected():
    illegal = [
        (S.TRIAL, S.PAST_DUE),    # TRIAL 은 결제 성공 시 ACTIVE 로만 (past_due 직행 불가)
        (S.TRIAL, S.TRIAL),       # 자기 자신 (no-op 전이 불허)
        (S.ACTIVE, S.TRIAL),      # 역행
        (S.ACTIVE, S.ACTIVE),     # 자기 자신
        (S.PAST_DUE, S.TRIAL),    # 역행
        (S.CANCELLED, S.ACTIVE),  # 종결에서 나감
        (S.CANCELLED, S.TRIAL),
        (S.EXPIRED, S.ACTIVE),    # 종결에서 나감
        (S.EXPIRED, S.PAST_DUE),
    ]
    for frm, to in illegal:
        assert can_transition(frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(frm, to)


def test_terminal_states_have_no_out_edges():
    assert TERMINAL == frozenset({S.CANCELLED, S.EXPIRED})
    for term in TERMINAL:
        assert is_terminal(term) is True
        for other in SubscriptionStatus:
            assert can_transition(term, other) is False


def test_non_terminal_states():
    for live in (S.TRIAL, S.ACTIVE, S.PAST_DUE):
        assert is_terminal(live) is False


def test_string_inputs_coerced():
    # status_cd 는 DB 에서 str 로 온다 — 문자열도 받아야 한다
    assert can_transition("TRIAL", "ACTIVE") is True
    assert can_transition("PAST_DUE", "ACTIVE") is True
    assert is_terminal("CANCELLED") is True


def test_unknown_status_is_false_not_crash():
    assert can_transition("BOGUS", "ACTIVE") is False
    with pytest.raises(IllegalTransition):
        assert_transition("BOGUS", "ACTIVE")
