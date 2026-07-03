"""포트(Protocol) — application 이 아는 외부 세계의 전부 (ARCHITECTURE §1).

구현체는 infrastructure/gateway/ 에. application/domain 은 SDK·HTTP 를 모른다.
프론트가 직접 못 부르는 외부 API 는 전부 이 포트 뒤에 있다 (중앙화 원칙 2·3).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.pattern import Verdict

# ── DTO (포트 계약의 일부) ─────────────────────────────────────────


@dataclass(frozen=True)
class PatternSpec:
    """문형 1개의 커리큘럼 정의 — 카탈로그(추후)가 채워서 넘긴다."""

    pattern_key: str          # 예: "conditional_perfect"
    name_en: str              # 예: "past conditional (would have + p.p.)"
    guide_en: str             # 튜터에게 주는 문형 설명/타겟 형태


@dataclass(frozen=True)
class ElicitDraft:
    """강제인출 질문 1개 — 해당 문형을 쓸 수밖에 없는 질문."""

    pattern_key: str
    question_en: str
    hint_en: str | None       # 초보 스캐폴딩용 (레벨별 노출은 svc 가 결정)


@dataclass(frozen=True)
class VerdictJudgment:
    verdict: Verdict
    evidence_quote: str       # 판정 근거가 된 발화 부분 (없으면 "")
    reason_ko: str


@dataclass(frozen=True)
class CorrectionDraft:
    """교정 리포트 항목 — severity 3단계 (기획서 C)."""

    quote: str                # 학습자 발화 원문 (오류보존 전사 그대로)
    severity_cd: str          # BLOCKING / GRAMMAR / POLISH
    corrected: str
    explain_ko: str


@dataclass(frozen=True)
class RingReportDraft:
    corrections: tuple[CorrectionDraft, ...]
    summary_ko: str


@dataclass(frozen=True)
class TranscriptLine:
    speaker_cd: str           # LEARNER / TUTOR
    text: str


@dataclass(frozen=True)
class RoomGrant:
    """실시간 방 입장권 — 앱이 아는 유일한 외부 접점 (단기 토큰)."""

    room_url: str
    token: str
    expires_ts: datetime


# ── Protocols ─────────────────────────────────────────────────────


class TutorPort(Protocol):
    """LLM 튜터 — 질문생성·판정·리포트 (Claude 구현체)."""

    async def make_elicit(self, spec: PatternSpec, topic_hint: str | None = None) -> ElicitDraft: ...

    async def judge_utterance(
        self, spec: PatternSpec, question_en: str, utterance_text: str
    ) -> VerdictJudgment: ...

    async def write_ring_report(
        self, transcript: tuple[TranscriptLine, ...], targets: tuple[PatternSpec, ...]
    ) -> RingReportDraft: ...


class SpeechPort(Protocol):
    """배치 전사 — 오류를 오류째 받아쓴다 (EPR 15/15 검증된 경로)."""

    async def transcribe_verbatim(self, audio: bytes, mime_type: str) -> str: ...


class PushPort(Protocol):
    """발신 트리거 — VoIP/ALERT push (실구현은 앱 생기는 E5)."""

    async def send_ring_push(
        self, push_token: str, kind_cd: str, ring_id: uuid.UUID
    ) -> None: ...


class RingPort(Protocol):
    """실시간 방 토큰 발급 (실구현은 voice 워커 붙는 E4)."""

    async def mint_room_grant(self, ring_id: uuid.UUID, learner_id: uuid.UUID) -> RoomGrant: ...
