"""SpeechPort 구현 — Gemini 배치 전사 (오류보존).

파일럿(pilot/)에서 EPR 15/15 검증된 경로·프롬프트를 그대로 계승 (lr-bb83b8cf).
좋은 STT 일수록 문법을 몰래 고친다 — verbatim 지시가 제품의 생명선.
"""
from __future__ import annotations

from google import genai
from google.genai import types

# 파일럿 검증 프롬프트 (pilot/bench.py 와 동일 취지 — 여기가 서버측 정본)
VERBATIM_INSTRUCTION = (
    "Transcribe this audio exactly as spoken, word for word. "
    "Preserve all grammatical errors, disfluencies, and non-native phrasing verbatim. "
    "Do NOT correct grammar, tense, articles, or word choice. "
    "Output only the transcription."
)


class GeminiSpeech:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def transcribe_verbatim(self, audio: bytes, mime_type: str) -> str:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                VERBATIM_INSTRUCTION,
                types.Part.from_bytes(data=audio, mime_type=mime_type),
            ],
        )
        return (response.text or "").strip()
