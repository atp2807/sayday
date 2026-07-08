"""ring_state 순수 유닛 — 전이표만 검증 (PG 불필요, IO 0)."""
import pytest

from sayday_server.domain.ring_state import (
    TERMINAL,
    IllegalTransition,
    RingStatus,
    assert_transition,
    can_transition,
    is_terminal,
)

S = RingStatus

# (frm, to) — 허용되는 전이 전부
LEGAL = [
    (S.SCHEDULED, S.RINGING),
    (S.SCHEDULED, S.MISSED),
    (S.SCHEDULED, S.DECLINED),
    (S.RINGING, S.IN_CALL),
    (S.RINGING, S.MISSED),
    (S.RINGING, S.DECLINED),
    (S.RINGING, S.DROPPED),
    (S.IN_CALL, S.ENDED),
    (S.IN_CALL, S.DROPPED),
    (S.ENDED, S.REPORTED),
]


@pytest.mark.parametrize("frm,to", LEGAL)
def test_legal_transitions_allowed(frm, to):
    assert can_transition(frm, to) is True
    assert_transition(frm, to)  # raise 안 함


def test_illegal_transitions_rejected():
    illegal = [
        (S.SCHEDULED, S.IN_CALL),   # 링잉 건너뜀
        (S.SCHEDULED, S.ENDED),
        (S.RINGING, S.ENDED),       # 인콜 건너뜀
        (S.ENDED, S.RINGING),       # 역행
        (S.IN_CALL, S.REPORTED),    # ENDED 건너뜀
        (S.REPORTED, S.RINGING),    # 종결에서 나감
        (S.MISSED, S.RINGING),
        (S.DECLINED, S.IN_CALL),
        (S.DROPPED, S.ENDED),
    ]
    for frm, to in illegal:
        assert can_transition(frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(frm, to)


def test_terminal_states_have_no_out_edges():
    assert TERMINAL == frozenset({S.REPORTED, S.MISSED, S.DECLINED, S.DROPPED})
    for term in TERMINAL:
        assert is_terminal(term) is True
        for other in RingStatus:
            assert can_transition(term, other) is False


def test_string_inputs_coerced():
    # status_cd 는 DB 에서 str 로 온다 — 문자열도 받아야 한다
    assert can_transition("SCHEDULED", "RINGING") is True
    assert can_transition("ENDED", "REPORTED") is True
    assert is_terminal("REPORTED") is True


def test_unknown_status_is_false_not_crash():
    assert can_transition("BOGUS", "RINGING") is False
    with pytest.raises(IllegalTransition):
        assert_transition("BOGUS", "RINGING")
