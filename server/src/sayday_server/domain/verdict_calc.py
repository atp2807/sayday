"""verdict_calc — 판정 정규화 + 회피 연속 감지.

판정 자체는 tutor_gateway(LLM)가 내리고, 여기서는
(1) 게이트웨이 출력의 도메인 정규화·검증
(2) 회피 연속(AVOIDED streak) → 다음 통화 강제 재등장 규칙
만 다룬다. 순수 함수만.
"""
from __future__ import annotations

from collections.abc import Sequence

from .pattern import RecallEntry, Verdict

# 회피가 이 횟수 연속되면 다음 ring 에 FORCED 로 심는다
FORCE_AFTER_AVOIDED_STREAK = 2


def parse_verdict(raw: str) -> Verdict:
    """게이트웨이 출력 정규화 — 미지 값은 예외 (조용히 삼키지 않는다)."""
    normalized = raw.strip().upper()
    try:
        return Verdict(normalized)
    except ValueError as exc:
        raise ValueError(f"unknown verdict: {raw!r}") from exc


def avoided_streak(entries: Sequence[RecallEntry]) -> int:
    """최신순 정렬된 이력에서, 가장 최근부터 연속된 AVOIDED 수.

    entries 는 recalled_ts 내림차순(최신 먼저)이어야 한다.
    정렬은 여기서 강제하지 않는다 — 호출자(repo)가 정렬해 오는 계약.
    """
    streak = 0
    for e in entries:
        if e.verdict is not Verdict.AVOIDED:
            break
        streak += 1
    return streak


def should_force(entries: Sequence[RecallEntry]) -> bool:
    """회피 연속 → 다음 통화 강제 포함 여부 (기획서: 회피 탐지의 단발 primitive)."""
    return avoided_streak(entries) >= FORCE_AFTER_AVOIDED_STREAK
