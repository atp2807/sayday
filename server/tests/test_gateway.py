"""게이트웨이 — 페이크 계약 + 실어댑터 통합(키 있을 때만).

실어댑터가 포트 시그니처를 만족하는지는 타입 주석으로 고정
(잘못되면 import/할당 시점 또는 mypy 에서 잡힘).
"""
import os
import uuid

import pytest

from sayday_server.application.ports import (
    PatternSpec,
    PushPort,
    RingPort,
    SpeechPort,
    TranscriptLine,
    TutorPort,
)
from sayday_server.config import Settings
from sayday_server.domain.pattern import Verdict
from sayday_server.infrastructure.gateway.factory import (
    build_push,
    build_ring,
    build_speech,
    build_tutor,
)
from sayday_server.infrastructure.gateway.fakes import FakeTutor

pytestmark = pytest.mark.asyncio

SPEC = PatternSpec(
    pattern_key="conditional_perfect",
    name_en="past conditional (would have + p.p.)",
    guide_en="If + past perfect, would have + past participle",
)


# ── 포트 계약 (페이크로) ──

async def test_fake_tutor_elicit_and_report_shapes():
    tutor: TutorPort = FakeTutor()
    elicit = await tutor.make_elicit(SPEC)
    assert elicit.pattern_key == SPEC.pattern_key and elicit.question_en

    report = await tutor.write_ring_report(
        transcript=(
            TranscriptLine("TUTOR", "What would you have done?"),
            TranscriptLine("LEARNER", "I would passed the exam."),
        ),
        targets=(SPEC,),
    )
    assert len(report.corrections) == 1  # LEARNER 발화만 교정
    assert report.corrections[0].severity_cd in {"BLOCKING", "GRAMMAR", "POLISH"}


async def test_fake_tutor_verdict_injection():
    tutor = FakeTutor(verdict_by_text={"English is important.": Verdict.AVOIDED})
    j = await tutor.judge_utterance(SPEC, "q?", "English is important.")
    assert j.verdict is Verdict.AVOIDED


async def test_factory_returns_fakes_without_keys():
    cfg = Settings(env="test", anthropic_api_key="", gemini_api_key="")
    tutor: TutorPort = build_tutor(cfg)
    speech: SpeechPort = build_speech(cfg)
    push: PushPort = build_push(cfg)
    ring: RingPort = build_ring(cfg)
    grant = await ring.mint_room_grant(uuid.uuid4(), uuid.uuid4())
    assert grant.token.startswith("fake-")
    await push.send_ring_push("tok", "VOIP", uuid.uuid4())
    assert (await speech.transcribe_verbatim(b"", "audio/mp4")).strip()
    assert tutor is not None


# ── 실어댑터 통합 (키 있을 때만 — CI/로컬에서 키 넣고 실행) ──

needs_claude = pytest.mark.skipif(
    not os.environ.get("SAYDAY_ANTHROPIC_API_KEY"), reason="SAYDAY_ANTHROPIC_API_KEY 없음"
)
needs_gemini = pytest.mark.skipif(
    not os.environ.get("SAYDAY_GEMINI_API_KEY"), reason="SAYDAY_GEMINI_API_KEY 없음"
)


@needs_claude
async def test_live_claude_judges_attempted_vs_avoided():
    from sayday_server.infrastructure.gateway.claude_tutor import ClaudeTutor

    tutor = ClaudeTutor(api_key=os.environ["SAYDAY_ANTHROPIC_API_KEY"])
    q = "If you hadn't started learning English, what would your career look like?"

    attempted = await tutor.judge_utterance(SPEC, q, "If I didn't start English, I would passed the exam.")
    assert attempted.verdict is Verdict.ATTEMPTED

    avoided = await tutor.judge_utterance(SPEC, q, "English is very important for my job.")
    assert avoided.verdict is Verdict.AVOIDED

    used = await tutor.judge_utterance(SPEC, q, "If I hadn't started, I would have become a chef.")
    assert used.verdict is Verdict.USED


@needs_claude
async def test_live_claude_report_severity():
    from sayday_server.infrastructure.gateway.claude_tutor import ClaudeTutor

    tutor = ClaudeTutor(api_key=os.environ["SAYDAY_ANTHROPIC_API_KEY"])
    report = await tutor.write_ring_report(
        transcript=(
            TranscriptLine("TUTOR", "Tell me about your weekend."),
            TranscriptLine("LEARNER", "I have went to Busan last weekend."),
        ),
        targets=(SPEC,),
    )
    assert report.corrections and report.summary_ko
    assert any("went" in c.quote for c in report.corrections)


@needs_gemini
async def test_live_gemini_transcribe_smoke():
    from pathlib import Path

    from sayday_server.infrastructure.gateway.gemini_speech import GeminiSpeech

    rec = Path(__file__).parents[2] / "pilot" / "audio" / "_live" / "rec1.m4a"
    if not rec.exists():
        pytest.skip("파일럿 녹음 없음")
    speech = GeminiSpeech(api_key=os.environ["SAYDAY_GEMINI_API_KEY"])
    text = await speech.transcribe_verbatim(rec.read_bytes(), "audio/mp4")
    assert "have went" in text.lower()  # 오류보존 (EPR) — 파일럿과 동일 기준
