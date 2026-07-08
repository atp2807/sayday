"""LearningRepo 구현 — learning 스키마 전용 (raw SQL, ORM 모델 없음: E3.5 결정).

자기 스키마만 접근한다 (learning.*). fsrs_card 는 py-fsrs 의 Card.to_dict()/
from_dict() 로 직렬화 (card.py: 무손실 round-trip 실측). 조회용 컬럼(fsrs_due_ts·
fsrs_stability)은 카드에서 파생해 별도 기록하고, 전체 카드는 fsrs_card jsonb 에 담는다.
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from fsrs import Card
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.pattern import PatternCard, RecallEntry, Verdict


def _to_card(m: RowMapping) -> PatternCard:
    return PatternCard(
        pattern_key=m["pattern_key"],
        fsrs_card=Card.from_dict(m["fsrs_card"]),
        recall_window_ms=m["recall_window_ms"],
    )


class SqlLearningRepo:
    """learning 스키마 repo — AsyncSession 주입 (UoW 가 공유 세션 전달)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_cards(self, learner_id: UUID) -> list[PatternCard]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT pattern_key, fsrs_card, recall_window_ms "
                    "FROM learning.pattern_card WHERE learner_id = :lid"
                ),
                {"lid": learner_id},
            )
        ).mappings().all()
        return [_to_card(m) for m in rows]

    async def get_card(self, learner_id: UUID, pattern_key: str) -> PatternCard | None:
        m = (
            await self._s.execute(
                text(
                    "SELECT pattern_key, fsrs_card, recall_window_ms "
                    "FROM learning.pattern_card "
                    "WHERE learner_id = :lid AND pattern_key = :pk"
                ),
                {"lid": learner_id, "pk": pattern_key},
            )
        ).mappings().first()
        return _to_card(m) if m is not None else None

    async def save_card(self, learner_id: UUID, card: PatternCard) -> UUID:
        # 신규 카드는 stability 가 None → 조회용 컬럼은 NOT NULL 이라 0.0 으로.
        stability = card.fsrs_card.stability or 0.0
        card_json = json.dumps(card.fsrs_card.to_dict())
        row_id = (
            await self._s.execute(
                text(
                    "INSERT INTO learning.pattern_card "
                    "(learner_id, pattern_key, fsrs_due_ts, fsrs_stability, "
                    "fsrs_card, recall_window_ms) "
                    "VALUES (:lid, :pk, :due, :stab, CAST(:card AS jsonb), :win) "
                    "ON CONFLICT (learner_id, pattern_key) DO UPDATE SET "
                    "fsrs_due_ts = EXCLUDED.fsrs_due_ts, "
                    "fsrs_stability = EXCLUDED.fsrs_stability, "
                    "fsrs_card = EXCLUDED.fsrs_card, "
                    "recall_window_ms = EXCLUDED.recall_window_ms "
                    "RETURNING id"
                ),
                {
                    "lid": learner_id,
                    "pk": card.pattern_key,
                    "due": card.fsrs_card.due,
                    "stab": stability,
                    "card": card_json,
                    "win": card.recall_window_ms,
                },
            )
        ).scalar_one()
        assert isinstance(row_id, UUID)
        return row_id

    async def list_due_cards(self, learner_id: UUID, now: datetime) -> list[PatternCard]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT pattern_key, fsrs_card, recall_window_ms "
                    "FROM learning.pattern_card "
                    "WHERE learner_id = :lid AND fsrs_due_ts <= :now"
                ),
                {"lid": learner_id, "now": now},
            )
        ).mappings().all()
        return [_to_card(m) for m in rows]

    async def add_recall(
        self,
        learner_id: UUID,
        pattern_card_id: UUID,
        ring_id: UUID | None,
        entry: RecallEntry,
        rating_cd: str,
    ) -> None:
        # created_ts = recalled_ts: recent_recalls 정렬(최신순)이 발화시각을 따르도록.
        await self._s.execute(
            text(
                "INSERT INTO learning.recall_entry "
                "(learner_id, pattern_card_id, ring_id, verdict_cd, "
                "response_ms, rating_cd, created_ts) "
                "VALUES (:lid, :pcid, :rid, :vc, :rms, :rc, :ts)"
            ),
            {
                "lid": learner_id,
                "pcid": pattern_card_id,
                "rid": ring_id,
                "vc": entry.verdict.value,
                "rms": entry.response_ms,
                "rc": rating_cd,
                "ts": entry.recalled_ts,
            },
        )

    async def recent_recalls(
        self, learner_id: UUID, pattern_key: str, limit: int = 10
    ) -> list[RecallEntry]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT re.verdict_cd, re.response_ms, re.created_ts "
                    "FROM learning.recall_entry re "
                    "JOIN learning.pattern_card pc ON pc.id = re.pattern_card_id "
                    "WHERE re.learner_id = :lid AND pc.pattern_key = :pk "
                    "ORDER BY re.created_ts DESC LIMIT :lim"
                ),
                {"lid": learner_id, "pk": pattern_key, "lim": limit},
            )
        ).mappings().all()
        return [
            RecallEntry(
                pattern_key=pattern_key,
                verdict=Verdict(m["verdict_cd"]),
                response_ms=m["response_ms"],
                recalled_ts=m["created_ts"],
            )
            for m in rows
        ]
