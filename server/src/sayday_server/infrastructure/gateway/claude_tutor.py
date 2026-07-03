"""TutorPort 구현 — Claude (질문생성·판정·리포트).

- 판정 3분류·리포트 3단계는 파일럿에서 로직 검증 완료 (lr-76ea78ce).
- structured outputs(messages.parse + pydantic)로 스키마 강제 — 파싱 취약성 제거.
- 키는 서버 env 에만 (SAYDAY_ANTHROPIC_API_KEY). 앱은 이 존재를 모른다.
"""
from __future__ import annotations

from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from ...application.ports import (
    CorrectionDraft,
    ElicitDraft,
    PatternSpec,
    RingReportDraft,
    TranscriptLine,
    VerdictJudgment,
)
from ...domain.pattern import Verdict

_MODEL = "claude-opus-4-8"

_SYSTEM = (
    "You are the tutor engine of sayday, a phone-English drill service for Korean learners. "
    "You are precise and never invent utterances the learner did not say."
)


# ── structured output 스키마 (Claude 응답 강제) ──

class _ElicitOut(BaseModel):
    question_en: str
    hint_en: str


class _VerdictOut(BaseModel):
    verdict: Literal["USED", "AVOIDED", "ATTEMPTED"]
    evidence_quote: str
    reason_ko: str


class _CorrectionOut(BaseModel):
    quote: str
    severity_cd: Literal["BLOCKING", "GRAMMAR", "POLISH"]
    corrected: str
    explain_ko: str


class _ReportOut(BaseModel):
    corrections: list[_CorrectionOut]
    summary_ko: str


class ClaudeTutor:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def make_elicit(self, spec: PatternSpec, topic_hint: str | None = None) -> ElicitDraft:
        topic = f' Base the question on this topic: "{topic_hint}".' if topic_hint else ""
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Write ONE conversational question that forces the learner to use "
                    f"the target pattern: {spec.name_en} ({spec.guide_en}). "
                    f"The question must be answerable only (or most naturally) with that pattern."
                    f"{topic} Also write a short hint showing the pattern frame "
                    f'(e.g. "If I had ..., I would have ...").'
                ),
            }],
            output_format=_ElicitOut,
        )
        out: _ElicitOut = response.parsed_output
        return ElicitDraft(
            pattern_key=spec.pattern_key, question_en=out.question_en, hint_en=out.hint_en
        )

    async def judge_utterance(
        self, spec: PatternSpec, question_en: str, utterance_text: str
    ) -> VerdictJudgment:
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Target pattern: {spec.name_en} ({spec.guide_en})\n"
                    f"Question asked: {question_en}\n"
                    f'Learner utterance (verbatim, errors preserved): "{utterance_text}"\n\n'
                    "Classify exactly one of:\n"
                    "- USED: the target pattern was produced (form correct enough to count)\n"
                    "- ATTEMPTED: the learner tried the structure but with form errors "
                    "(e.g. 'would passed') — this is NOT avoidance\n"
                    "- AVOIDED: the learner answered without the structure, "
                    "escaping to simpler grammar\n"
                    "evidence_quote = the exact substring that shows your verdict ('' if AVOIDED). "
                    "reason_ko = 한 문장, 한국어."
                ),
            }],
            output_format=_VerdictOut,
        )
        out: _VerdictOut = response.parsed_output
        return VerdictJudgment(
            verdict=Verdict(out.verdict),
            evidence_quote=out.evidence_quote,
            reason_ko=out.reason_ko,
        )

    async def write_ring_report(
        self, transcript: tuple[TranscriptLine, ...], targets: tuple[PatternSpec, ...]
    ) -> RingReportDraft:
        lines = "\n".join(f"[{t.speaker_cd}] {t.text}" for t in transcript)
        target_desc = "; ".join(f"{s.pattern_key}={s.name_en}" for s in targets)
        response = await self._client.messages.parse(
            model=_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Below is a verbatim call transcript (learner errors preserved by STT).\n"
                    f"Target patterns this call: {target_desc}\n\n{lines}\n\n"
                    "Write the post-call correction report for LEARNER utterances only.\n"
                    "severity_cd: BLOCKING(뜻이 안 통함) / GRAMMAR(문법 틀림, 뜻은 통함) / "
                    "POLISH(맞지만 더 자연스러운 표현).\n"
                    "Rules: quote must be the exact learner text. Do not flag the tutor. "
                    "Do not invent errors. explain_ko/summary_ko 는 한국어, 간결하게. "
                    "summary_ko 는 잘한 것 1개 + 다음에 집중할 것 1개."
                ),
            }],
            output_format=_ReportOut,
        )
        out: _ReportOut = response.parsed_output
        return RingReportDraft(
            corrections=tuple(
                CorrectionDraft(
                    quote=c.quote, severity_cd=c.severity_cd,
                    corrected=c.corrected, explain_ko=c.explain_ko,
                )
                for c in out.corrections
            ),
            summary_ko=out.summary_ko,
        )
