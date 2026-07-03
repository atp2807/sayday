"""drill_calc(커리큘럼 배치) + verdict_calc(회피 연속) 행동 검증."""
import random
from datetime import UTC, datetime, timedelta

import pytest

from sayday_server.domain.drill_calc import plan_ring
from sayday_server.domain.pattern import RecallEntry, StepKind, Verdict
from sayday_server.domain.recall_calc import apply_recall, new_pattern_card
from sayday_server.domain.verdict_calc import (
    avoided_streak,
    parse_verdict,
    should_force,
)

NOW = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)


def _card(key: str, due_days_ago: float = 0.0):
    """due 가 과거인 카드 (due_days_ago 일 전)."""
    card = new_pattern_card(key, NOW - timedelta(days=due_days_ago + 1))
    out = apply_recall(card, Verdict.USED, 3_000, NOW - timedelta(days=due_days_ago + 1))
    c = out.card
    c.fsrs_card.due = NOW - timedelta(days=due_days_ago)
    return c


# ── verdict_calc ──

def test_parse_verdict_normalizes_and_rejects_unknown():
    assert parse_verdict(" used ") is Verdict.USED
    with pytest.raises(ValueError):
        parse_verdict("MAYBE")


def _entry(v: Verdict, days_ago: int) -> RecallEntry:
    return RecallEntry(
        pattern_key="p", verdict=v, response_ms=None,
        recalled_ts=NOW - timedelta(days=days_ago),
    )


def test_avoided_streak_counts_from_latest():
    entries = [_entry(Verdict.AVOIDED, 0), _entry(Verdict.AVOIDED, 1), _entry(Verdict.USED, 2)]
    assert avoided_streak(entries) == 2
    assert should_force(entries)


def test_streak_broken_by_attempted():
    # ATTEMPTED 는 회피가 아니다 — streak 을 끊는다
    entries = [_entry(Verdict.AVOIDED, 0), _entry(Verdict.ATTEMPTED, 1), _entry(Verdict.AVOIDED, 2)]
    assert avoided_streak(entries) == 1
    assert not should_force(entries)


# ── drill_calc ──

def test_priority_forced_then_overdue_then_new():
    cards = [_card("a", due_days_ago=1), _card("b", due_days_ago=5), _card("c", due_days_ago=3)]
    plan = plan_ring(
        cards=cards, forced_keys=["a"], new_pool=["fresh1", "fresh2"],
        now=NOW, rng=random.Random(1),
    )
    kinds = {s.pattern_key: s.kind for s in plan.steps}
    assert kinds["a"] is StepKind.FORCED
    assert kinds["b"] is StepKind.REVIEW and kinds["c"] is StepKind.REVIEW
    assert kinds["fresh1"] is StepKind.NEW
    # 통화당 문형 4개 (capacity) — fresh2 는 못 들어옴
    assert set(plan.pattern_keys) == {"a", "b", "c", "fresh1"}
    # due 정렬: 더 연체된 b 가 c 보다 먼저 선정됐는지는 순서가 아니라 포함으로 충분
    # (인터리빙이 순서를 섞으므로)


def test_reviews_crowd_out_new_when_capacity_full():
    cards = [_card(k, due_days_ago=i + 1) for i, k in enumerate("abcd")]
    plan = plan_ring(cards=cards, forced_keys=[], new_pool=["fresh"], now=NOW,
                     rng=random.Random(1))
    assert "fresh" not in plan.pattern_keys  # 복습이 밀리면 신규 0 (기획 원칙)


def test_new_pattern_gets_start_window():
    plan = plan_ring(cards=[], forced_keys=[], new_pool=["fresh"], now=NOW,
                     rng=random.Random(1))
    (step,) = plan.steps[:1]
    assert step.kind is StepKind.NEW and step.recall_window_ms == 12_000


def test_interleave_no_adjacent_same_pattern():
    cards = [_card(k, due_days_ago=1) for k in "abc"]
    for seed in range(30):
        plan = plan_ring(cards=cards, forced_keys=[], new_pool=[], now=NOW,
                         rng=random.Random(seed))
        keys = [s.pattern_key for s in plan.steps]
        assert len(keys) == 6  # 3문형 × reps 2
        assert all(x != y for x, y in zip(keys, keys[1:])), f"seed={seed}: {keys}"


def test_each_pattern_repeated_reps_times():
    cards = [_card(k, due_days_ago=1) for k in "ab"]
    plan = plan_ring(cards=cards, forced_keys=[], new_pool=[], now=NOW,
                     rng=random.Random(7))
    keys = [s.pattern_key for s in plan.steps]
    assert keys.count("a") == 2 and keys.count("b") == 2


def test_deterministic_given_seed():
    cards = [_card(k, due_days_ago=1) for k in "abc"]
    p1 = plan_ring(cards=cards, forced_keys=[], new_pool=[], now=NOW, rng=random.Random(42))
    p2 = plan_ring(cards=cards, forced_keys=[], new_pool=[], now=NOW, rng=random.Random(42))
    assert p1 == p2


def test_single_pattern_allows_adjacency():
    cards = [_card("only", due_days_ago=1)]
    plan = plan_ring(cards=cards, forced_keys=[], new_pool=[], now=NOW,
                     rng=random.Random(1))
    assert [s.pattern_key for s in plan.steps] == ["only", "only"]
