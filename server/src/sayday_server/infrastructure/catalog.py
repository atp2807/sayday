"""CatalogPort 인메모리 구현 — dev/test 용. 실 카탈로그 테이블은 나중.

pattern_key -> PatternSpec 매핑 + 도입 후보 우선순위 리스트. get_spec 이 없는 키면
KeyError (조용히 삼키지 않는다 — svc 가 처리). 시드는 소규모 문형 몇 개.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..application.ports import PatternSpec

# dev/test 시드 — 우선순위순(new_pool 이 이 순서대로 도입 후보를 낸다)
_SEED: tuple[PatternSpec, ...] = (
    PatternSpec(
        pattern_key="used_to",
        name_en="used to + base verb (past habit/state)",
        guide_en="Talk about a repeated past habit or a state that is no longer true.",
    ),
    PatternSpec(
        pattern_key="present_perfect_experience",
        name_en="present perfect (have + p.p.) for life experience",
        guide_en="Describe an experience at an unspecified time (Have you ever...).",
    ),
    PatternSpec(
        pattern_key="conditional_perfect",
        name_en="past conditional (would have + p.p.)",
        guide_en="Talk about an unreal past outcome (what would have happened if...).",
    ),
    PatternSpec(
        pattern_key="reported_speech",
        name_en="reported speech (said that + backshift)",
        guide_en="Report what someone else said, backshifting the tense.",
    ),
    PatternSpec(
        pattern_key="relative_clause_which",
        name_en="non-defining relative clause (, which ...)",
        guide_en="Add extra information about a whole clause using ', which'.",
    ),
)


class InMemoryCatalog:
    """dict 기반 카탈로그 + 도입 후보 순서 리스트."""

    def __init__(self, specs: Sequence[PatternSpec] = _SEED) -> None:
        self._by_key: dict[str, PatternSpec] = {s.pattern_key: s for s in specs}
        self._order: list[str] = [s.pattern_key for s in specs]

    async def get_spec(self, pattern_key: str) -> PatternSpec:
        return self._by_key[pattern_key]  # 없으면 KeyError

    async def new_pool(self, have_keys: Sequence[str]) -> list[str]:
        have = set(have_keys)
        return [k for k in self._order if k not in have]
