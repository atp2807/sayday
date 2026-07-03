"""recall_calc — FSRS(간격) + latency-dial(응답 허용시간) 갱신. 엔진 심장.

원리 매핑 (기획서 + lr-e3f39673):
- 분산: FSRS-6이 문형 재등장 간격을 계산 (며칠에 걸쳐 다시 등장)
- 무압박 사고시간 → 자동화: recall_window 가 넉넉하게 시작해서
  인출 성공마다 줄어들고, floor(실시간 수준)에 닿으면 자동화로 간주.
  "사고 시간은 걸림돌이 아니라 엔진" — 시간을 주고, 점점 회수한다.

순수 함수만. IO/프레임워크 import 금지 (py-fsrs = 순수 계산 라이브러리).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from fsrs import Card, Rating, Scheduler

from .pattern import PatternCard, Verdict

# ── latency-dial 상수 (튜닝 대상 — 근거: 새 문형은 무압박, 자동화 목표는 실시간) ──
WINDOW_START_MS = 12_000   # 신규 문형 시작 허용시간 (넉넉한 사고시간)
WINDOW_FLOOR_MS = 3_000    # 실시간 대화 수준 = 자동화 판정선
WINDOW_SHRINK_RATE = 0.8   # USED(제한시간 내) → 다음엔 20% 덜 준다
WINDOW_RELAX_RATE = 1.25   # AVOIDED → 다시 여유를 준다 (좌절 방지)

# 자동화 판정: window 가 floor 에 닿고, 기억 안정성이 이 이상
AUTOMATIZED_MIN_STABILITY = 7.0  # days (FSRS stability)

_scheduler = Scheduler(enable_fuzzing=False)  # 순수성: 동일 입력 → 동일 출력


def new_pattern_card(pattern_key: str, now: datetime) -> PatternCard:
    """신규 문형 카드 — 넉넉한 recall_window 로 시작."""
    card = Card()
    card.due = now
    return PatternCard(
        pattern_key=pattern_key,
        fsrs_card=card,
        recall_window_ms=WINDOW_START_MS,
    )


def rating_for(verdict: Verdict, response_ms: int | None, window_ms: int) -> Rating:
    """판정 + 응답속도 → FSRS 평점.

    USED(제한 내)=Good / USED(느림)=Hard / ATTEMPTED=Hard / AVOIDED=Again.
    ATTEMPTED 을 Again 으로 떨어뜨리지 않는 이유: 구조를 시도한 것은
    인출 실패가 아니라 형태 오류 — 처리 경로가 다르다 (lr-76ea78ce).
    """
    if verdict is Verdict.AVOIDED:
        return Rating.Again
    if verdict is Verdict.ATTEMPTED:
        return Rating.Hard
    # USED
    if response_ms is None or response_ms > window_ms:
        return Rating.Hard  # 인출은 됐으나 느림 — 아직 자동화 아님
    return Rating.Good


def next_window_ms(verdict: Verdict, response_ms: int | None, window_ms: int) -> int:
    """latency-dial: 다음 인출의 허용 사고시간.

    - USED & 제한 내  → 조인다 (floor 까지)
    - USED & 느림     → 유지 (속도가 아직이므로 더 조이지 않는다)
    - ATTEMPTED       → 유지 (형태 교정이 먼저)
    - AVOIDED         → 푼다 (start 상한) — 압박이 회피를 낳았을 수 있다
    """
    if verdict is Verdict.AVOIDED:
        return min(WINDOW_START_MS, round(window_ms * WINDOW_RELAX_RATE))
    if verdict is Verdict.USED and response_ms is not None and response_ms <= window_ms:
        return max(WINDOW_FLOOR_MS, round(window_ms * WINDOW_SHRINK_RATE))
    return window_ms


@dataclass(frozen=True)
class RecallOutcome:
    card: PatternCard
    rating: Rating


def apply_recall(
    card: PatternCard,
    verdict: Verdict,
    response_ms: int | None,
    now: datetime,
) -> RecallOutcome:
    """인출 1회 반영 — FSRS 상태와 recall_window 를 함께 갱신한 새 카드를 반환.

    입력 card 는 변경하지 않는다 (불변 갱신).
    """
    rating = rating_for(verdict, response_ms, card.recall_window_ms)
    reviewed, _ = _scheduler.review_card(card.fsrs_card, rating, now)
    updated = replace(
        card,
        fsrs_card=reviewed,
        recall_window_ms=next_window_ms(verdict, response_ms, card.recall_window_ms),
    )
    return RecallOutcome(card=updated, rating=rating)


def is_automatized(card: PatternCard) -> bool:
    """자동화 판정 — window 가 실시간 수준 + 기억이 안정."""
    stability = card.fsrs_card.stability or 0.0
    return (
        card.recall_window_ms <= WINDOW_FLOOR_MS
        and stability >= AUTOMATIZED_MIN_STABILITY
    )


def is_due(card: PatternCard, now: datetime) -> bool:
    return card.fsrs_card.due <= now
