"""게이트웨이 조립 지점 (composition root) — 키 있으면 실어댑터, 없으면 페이크.

application 은 포트만 안다. 실/페이크 선택은 여기 한 곳.
"""
from __future__ import annotations

from ...application.ports import PayPort, PushPort, RingPort, SpeechPort, TutorPort
from ...application.repos import CatalogPort, UowFactory
from ...config import Settings
from ..catalog import InMemoryCatalog
from ..db.engine import Db
from ..db.uow import SqlUowFactory
from .fakes import FakePay, FakeRing, FakeSpeech, FakeTutor, LogPush


def build_tutor(cfg: Settings) -> TutorPort:
    if cfg.anthropic_api_key:
        from .claude_tutor import ClaudeTutor

        return ClaudeTutor(api_key=cfg.anthropic_api_key)
    return FakeTutor()


def build_speech(cfg: Settings) -> SpeechPort:
    if cfg.gemini_api_key:
        from .gemini_speech import GeminiSpeech

        return GeminiSpeech(api_key=cfg.gemini_api_key, model=cfg.gemini_stt_model)
    return FakeSpeech()


def build_push(cfg: Settings) -> PushPort:
    return LogPush()  # 실구현 = E5 (앱 push 토큰 생기는 시점)


def build_ring(cfg: Settings) -> RingPort:
    return FakeRing()  # 실구현 = E4 (LiveKit 붙는 시점)


def build_pay(cfg: Settings) -> PayPort:
    # 실 PG 어댑터(Toss/PortOne 등)는 키/가맹점 종속 → 키 생기면 여기서 분기(아직 없음).
    return FakePay()


def build_uow_factory(db: Db) -> UowFactory:
    return SqlUowFactory(db)


def build_catalog(cfg: Settings) -> CatalogPort:
    return InMemoryCatalog()  # 실 카탈로그 테이블은 나중
