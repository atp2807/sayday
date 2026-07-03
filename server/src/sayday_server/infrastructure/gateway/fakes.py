"""페이크 어댑터 — 테스트·키 없는 dev 용. 포트 계약의 참조 구현.

PushPort/RingPort 실구현은 인프라가 붙는 단계에서 (push=E5 앱, ring=E4 voice 워커).
그 전까지 svc 개발·테스트는 전부 이걸로 돈다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ...application.ports import (
    CorrectionDraft,
    ElicitDraft,
    PatternSpec,
    RingReportDraft,
    RoomGrant,
    TranscriptLine,
    VerdictJudgment,
)
from ...domain.pattern import Verdict


@dataclass
class FakeTutor:
    """결정적 응답 — 시나리오 주입 가능."""

    verdict_by_text: dict[str, Verdict] = field(default_factory=dict)

    async def make_elicit(self, spec: PatternSpec, topic_hint: str | None = None) -> ElicitDraft:
        return ElicitDraft(
            pattern_key=spec.pattern_key,
            question_en=f"[fake] question forcing {spec.name_en}",
            hint_en=f"[fake] frame of {spec.pattern_key}",
        )

    async def judge_utterance(
        self, spec: PatternSpec, question_en: str, utterance_text: str
    ) -> VerdictJudgment:
        verdict = self.verdict_by_text.get(utterance_text, Verdict.USED)
        return VerdictJudgment(verdict=verdict, evidence_quote=utterance_text, reason_ko="[fake]")

    async def write_ring_report(
        self, transcript: tuple[TranscriptLine, ...], targets: tuple[PatternSpec, ...]
    ) -> RingReportDraft:
        learner_lines = [t for t in transcript if t.speaker_cd == "LEARNER"]
        return RingReportDraft(
            corrections=tuple(
                CorrectionDraft(
                    quote=line.text, severity_cd="GRAMMAR",
                    corrected=f"[fixed] {line.text}", explain_ko="[fake]",
                )
                for line in learner_lines
            ),
            summary_ko="[fake] summary",
        )


@dataclass
class FakeSpeech:
    canned_text: str = "I have went to Busan last weekend."

    async def transcribe_verbatim(self, audio: bytes, mime_type: str) -> str:
        return self.canned_text


@dataclass
class LogPush:
    sent: list[tuple[str, str, uuid.UUID]] = field(default_factory=list)

    async def send_ring_push(self, push_token: str, kind_cd: str, ring_id: uuid.UUID) -> None:
        self.sent.append((push_token, kind_cd, ring_id))


@dataclass
class FakeRing:
    async def mint_room_grant(self, ring_id: uuid.UUID, learner_id: uuid.UUID) -> RoomGrant:
        return RoomGrant(
            room_url="wss://fake.livekit.local",
            token=f"fake-{ring_id}",
            expires_ts=datetime.now(UTC) + timedelta(seconds=60),
        )
