"""도메인 엔티티·값객체 — 외부 의존 0 (py-fsrs는 순수 계산 라이브러리로 허용).

용어는 docs/ARCHITECTURE.md §4 용어집을 따른다.
learner / ring / pattern_card / utterance / elicit_prompt / verdict / recall_window
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from fsrs import Card


class Verdict(str, Enum):
    """문형 사용 판정 — 파일럿에서 3분류 검증됨 (lr-76ea78ce)."""

    USED = "USED"            # 목표 문형 인출 성공
    ATTEMPTED = "ATTEMPTED"  # 구조는 시도, 형태 오류 → 교정 대상 (회피 아님)
    AVOIDED = "AVOIDED"      # 회피 — 쉬운 구조로 도망 → 재등장 스케줄 대상


class StepKind(str, Enum):
    """drill_plan 안에서 이 문형이 배치된 이유."""

    REVIEW = "REVIEW"  # FSRS due 복습
    NEW = "NEW"        # 신규 도입
    FORCED = "FORCED"  # 회피 연속으로 강제 재등장 (verdict_calc.should_force)


@dataclass
class PatternCard:
    """문형 1개의 학습 상태 = FSRS 카드 + latency-dial.

    recall_window_ms: 이 문형에 허용되는 응답 사고시간.
    새 문형은 넉넉하게 시작(무압박 사고시간), 인출이 성공할수록 줄어들어
    실시간 수준(floor)에 도달하면 자동화로 간주한다. — sayday 코어 메커니즘.
    """

    pattern_key: str          # 예: "conditional_perfect"
    fsrs_card: Card
    recall_window_ms: int

    @property
    def due_ts(self) -> datetime:
        return self.fsrs_card.due


@dataclass(frozen=True)
class RecallEntry:
    """recall_log 한 줄의 도메인 뷰 (판정 이력)."""

    pattern_key: str
    verdict: Verdict
    response_ms: int | None   # 질문 종료 → 발화 시작까지. 미발화면 None
    recalled_ts: datetime


@dataclass(frozen=True)
class ElicitStep:
    """통화 안에서 문형 1회 인출 시도 슬롯 (drill_plan의 원자)."""

    pattern_key: str
    kind: StepKind
    recall_window_ms: int


@dataclass(frozen=True)
class DrillPlan:
    """다음 ring 1건의 커리큘럼 — 인터리빙된 elicit 순서."""

    steps: tuple[ElicitStep, ...]

    @property
    def pattern_keys(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for s in self.steps:
            seen.setdefault(s.pattern_key)
        return tuple(seen)
