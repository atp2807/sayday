"""drill_calc — 다음 ring 1건의 커리큘럼 배치.

기획서 원리 매핑:
- 복습 → 신규 → (강제) 우선순위: FORCED(회피 연속) > due 복습(연체 오래된 순) > 신규
- 인터리빙: 같은 문형을 몰아서 묻지 않는다 — 문형들을 라운드로 섞어 배치

순수 함수만. 셔플이 필요한 자리는 random.Random 을 주입받아 결정적으로 유지.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime

from .pattern import DrillPlan, ElicitStep, PatternCard, StepKind
from .recall_calc import WINDOW_START_MS, is_due

RING_PATTERN_CAPACITY = 4   # 통화당 목표 문형 수 (기획서: 3~4개)
MAX_NEW_PER_RING = 1        # 통화당 신규 도입 상한 (복습이 밀리면 신규 0)
REPS_PER_PATTERN = 2        # 문형당 인출 시도 횟수


def plan_ring(
    cards: Sequence[PatternCard],
    forced_keys: Sequence[str],
    new_pool: Sequence[str],
    now: datetime,
    rng: random.Random,
    capacity: int = RING_PATTERN_CAPACITY,
    max_new: int = MAX_NEW_PER_RING,
    reps: int = REPS_PER_PATTERN,
) -> DrillPlan:
    """다음 통화의 문형 선정 + 인터리빙 배치.

    cards: learner 의 기존 pattern_card 전부
    forced_keys: verdict_calc.should_force 로 강제 재등장 지정된 문형
    new_pool: 아직 카드가 없는 도입 후보 문형 (우선순위순)
    """
    by_key = {c.pattern_key: c for c in cards}
    picked: list[ElicitStep] = []
    picked_keys: set[str] = set()

    def _pick(key: str, kind: StepKind, window_ms: int) -> None:
        if key in picked_keys or len(picked) >= capacity:
            return
        picked_keys.add(key)
        picked.append(ElicitStep(pattern_key=key, kind=kind, recall_window_ms=window_ms))

    # 1) 회피 강제 재등장 — 최우선
    for key in forced_keys:
        card = by_key.get(key)
        if card is not None:
            _pick(key, StepKind.FORCED, card.recall_window_ms)

    # 2) due 복습 — 연체 오래된 순
    due_cards = sorted(
        (c for c in cards if is_due(c, now) and c.pattern_key not in picked_keys),
        key=lambda c: c.fsrs_card.due,
    )
    for card in due_cards:
        _pick(card.pattern_key, StepKind.REVIEW, card.recall_window_ms)

    # 3) 신규 도입 — 남는 자리에, 상한 내에서
    new_count = 0
    for key in new_pool:
        if new_count >= max_new or len(picked) >= capacity:
            break
        if key not in picked_keys and key not in by_key:
            _pick(key, StepKind.NEW, WINDOW_START_MS)
            new_count += 1

    return DrillPlan(steps=tuple(_interleave(picked, reps, rng)))


def _interleave(
    patterns: Sequence[ElicitStep], reps: int, rng: random.Random
) -> list[ElicitStep]:
    """문형당 reps 회를 라운드로 섞는다. 인접 동일 문형 금지 (문형 2개 이상일 때).

    라운드마다 순서를 셔플하되, 이전 라운드 마지막과 다음 라운드 첫 문형이
    같지 않도록 회전시킨다 — blocked 반복 방지 (기획서 원리 2).
    """
    if not patterns:
        return []
    steps: list[ElicitStep] = []
    for _ in range(reps):
        round_order = list(patterns)
        rng.shuffle(round_order)
        if steps and len(round_order) > 1:
            while round_order[0].pattern_key == steps[-1].pattern_key:
                round_order.append(round_order.pop(0))  # 회전
        steps.extend(round_order)
    return steps
