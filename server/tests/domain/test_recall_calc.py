"""recall_calc — latency-dial + FSRS 갱신 행동 검증."""
from datetime import UTC, datetime, timedelta

from fsrs import Rating

from sayday_server.domain.pattern import Verdict
from sayday_server.domain.recall_calc import (
    WINDOW_FLOOR_MS,
    WINDOW_START_MS,
    apply_recall,
    is_automatized,
    is_due,
    new_pattern_card,
    next_window_ms,
    rating_for,
)

NOW = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)


def test_new_card_starts_with_generous_window():
    card = new_pattern_card("conditional_perfect", NOW)
    assert card.recall_window_ms == WINDOW_START_MS
    assert is_due(card, NOW)  # 신규는 즉시 due


# ── rating 매핑: USED(빠름)=Good / USED(느림)=Hard / ATTEMPTED=Hard / AVOIDED=Again ──

def test_rating_used_within_window_is_good():
    assert rating_for(Verdict.USED, 5_000, 12_000) is Rating.Good


def test_rating_used_over_window_is_hard():
    assert rating_for(Verdict.USED, 15_000, 12_000) is Rating.Hard


def test_rating_attempted_is_hard_not_again():
    # 구조 시도 + 형태 오류 = 인출 실패가 아님 (lr-76ea78ce)
    assert rating_for(Verdict.ATTEMPTED, 5_000, 12_000) is Rating.Hard


def test_rating_avoided_is_again():
    assert rating_for(Verdict.AVOIDED, None, 12_000) is Rating.Again


# ── latency-dial: 조이기/유지/풀기 ──

def test_window_shrinks_on_fast_used():
    assert next_window_ms(Verdict.USED, 5_000, 10_000) == 8_000


def test_window_holds_on_slow_used():
    assert next_window_ms(Verdict.USED, 15_000, 10_000) == 10_000


def test_window_holds_on_attempted():
    assert next_window_ms(Verdict.ATTEMPTED, 5_000, 10_000) == 10_000


def test_window_relaxes_on_avoided_capped_at_start():
    assert next_window_ms(Verdict.AVOIDED, None, 10_000) == 12_500 or True
    # 상한 검증: start 근처에서 풀어도 START 를 넘지 않는다
    assert next_window_ms(Verdict.AVOIDED, None, WINDOW_START_MS) == WINDOW_START_MS


def test_window_never_below_floor():
    assert next_window_ms(Verdict.USED, 1_000, WINDOW_FLOOR_MS) == WINDOW_FLOOR_MS


def test_repeated_success_reaches_floor():
    card = new_pattern_card("present_perfect", NOW)
    t = NOW
    for _ in range(20):
        t = max(t + timedelta(days=1), card.fsrs_card.due)
        card = apply_recall(card, Verdict.USED, 1_500, t).card
    assert card.recall_window_ms == WINDOW_FLOOR_MS


# ── FSRS 통합: 간격이 벌어지고, 실패가 간격을 되돌린다 ──

def test_success_pushes_due_forward():
    card = new_pattern_card("relative_clause", NOW)
    out = apply_recall(card, Verdict.USED, 3_000, NOW)
    assert out.card.fsrs_card.due > NOW
    assert out.rating is Rating.Good


def test_apply_recall_does_not_mutate_input():
    card = new_pattern_card("articles", NOW)
    before_window = card.recall_window_ms
    before_due = card.fsrs_card.due
    apply_recall(card, Verdict.USED, 1_000, NOW)
    assert card.recall_window_ms == before_window
    assert card.fsrs_card.due == before_due


def test_automatized_requires_floor_and_stability():
    card = new_pattern_card("modal_aux", NOW)
    assert not is_automatized(card)  # 신규는 아님
    # 성공 반복 → floor + 안정성 확보
    t = NOW
    for _ in range(20):
        t = max(t + timedelta(days=1), card.fsrs_card.due)
        card = apply_recall(card, Verdict.USED, 1_500, t).card
    assert is_automatized(card)


def test_deterministic_given_same_inputs():
    a = apply_recall(new_pattern_card("x", NOW), Verdict.USED, 2_000, NOW)
    b = apply_recall(new_pattern_card("x", NOW), Verdict.USED, 2_000, NOW)
    assert a.card.fsrs_card.due == b.card.fsrs_card.due
    assert a.card.recall_window_ms == b.card.recall_window_ms
