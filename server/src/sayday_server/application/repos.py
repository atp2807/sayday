"""repo 포트 + UoW + catalog (Protocol) — application 이 아는 영속 계층의 전부.

infrastructure/db 가 이 Protocol 들을 구현하고, composition root(gateway/factory)가
주입한다. application/domain 은 SQLAlchemy·asyncpg 를 모른다 (원칙 5).

§3: repo 는 자기 스키마만 (LearningRepo→learning.*, CallRepo→call.*).
    cross-schema 오케스트레이션은 서비스(2b)에서만. UoW 가 트랜잭션 경계.
"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime, time
from typing import Any, Protocol
from uuid import UUID

from ..domain.pattern import DrillPlan, PatternCard, RecallEntry
from .ports import PatternSpec
from .records import (
    PaymentRecord,
    PlanRecord,
    RingRecord,
    RingReportRecord,
    RingSlotRecord,
    SubscriptionRecord,
    UtteranceRecord,
)


class LearningRepo(Protocol):
    """learning 스키마 전용 (pattern_card·recall_entry)."""

    async def list_cards(self, learner_id: UUID) -> list[PatternCard]: ...

    async def get_card(self, learner_id: UUID, pattern_key: str) -> PatternCard | None: ...

    async def save_card(self, learner_id: UUID, card: PatternCard) -> UUID:
        """upsert (uq learner_id,pattern_key). pattern_card.id 반환."""
        ...

    async def list_due_cards(self, learner_id: UUID, now: datetime) -> list[PatternCard]:
        """fsrs_due_ts <= now 인 카드."""
        ...

    async def add_recall(
        self,
        learner_id: UUID,
        pattern_card_id: UUID,
        ring_id: UUID | None,
        entry: RecallEntry,
        rating_cd: str,
    ) -> None: ...

    async def recent_recalls(
        self, learner_id: UUID, pattern_key: str, limit: int = 10
    ) -> list[RecallEntry]:
        """최신순 — verdict_calc.should_force 입력."""
        ...


class CallRepo(Protocol):
    """call 스키마 전용 (ring_slot·ring·utterance·correction·ring_report)."""

    async def create_ring_slot(
        self,
        learner_id: UUID,
        days_of_week: int,
        local_time: time,
        tz_name: str,
        next_fire_ts: datetime | None,
    ) -> RingSlotRecord: ...

    async def list_active_slots_due(self, now: datetime) -> list[RingSlotRecord]:
        """active_yn AND next_fire_ts<=now (worker=admin 경로)."""
        ...

    async def set_slot_next_fire(self, slot_id: UUID, next_fire_ts: datetime | None) -> None: ...

    async def create_ring(
        self,
        learner_id: UUID,
        ring_slot_id: UUID | None,
        drill_plan: DrillPlan | None,
        room_grant_ref: str | None,
        scheduled_ts: datetime,
        status_cd: str,
    ) -> RingRecord: ...

    async def get_ring(self, ring_id: UUID) -> RingRecord | None: ...

    async def set_ring_status(
        self,
        ring_id: UUID,
        status_cd: str,
        *,
        started_ts: datetime | None = None,
        ended_ts: datetime | None = None,
        room_grant_ref: str | None = None,
    ) -> None: ...

    async def add_utterance(
        self,
        ring_id: UUID,
        learner_id: UUID,
        seq: int,
        speaker_cd: str,
        source_cd: str,
        text: str,
        target_pattern_key: str | None,
        response_ms: int | None,
    ) -> UtteranceRecord: ...

    async def list_utterances(self, ring_id: UUID) -> list[UtteranceRecord]:
        """seq 순."""
        ...

    async def set_utterance_verdict(self, utterance_id: UUID, verdict_cd: str) -> None: ...

    async def add_correction(
        self,
        ring_id: UUID,
        learner_id: UUID,
        utterance_id: UUID | None,
        severity_cd: str,
        original_text: str,
        corrected_text: str,
        note: str | None,
    ) -> None: ...

    async def create_ring_report(
        self, ring_id: UUID, learner_id: UUID, summary: str, metrics: dict[str, Any] | None
    ) -> RingReportRecord: ...

    async def get_ring_report(self, ring_id: UUID) -> RingReportRecord | None: ...


class AccountRepo(Protocol):
    """account 스키마 전용 (learner). 자격증명 없음 — auth 는 별도 스키마(§3)."""

    async def get_learner_level(self, learner_id: UUID) -> str | None:
        """진단 전은 None."""
        ...

    async def set_learner_level(self, learner_id: UUID, level_cd: str) -> None: ...


class BillingRepo(Protocol):
    """billing 스키마 전용 (plan·subscription·payment)."""

    async def list_active_plans(self) -> list[PlanRecord]:
        """active_yn 인 요금제 — 카탈로그 (참조테이블 RLS 로 app 도 SELECT 가능)."""
        ...

    async def get_plan(self, plan_key: str) -> PlanRecord | None: ...

    async def get_plan_by_id(self, plan_id: UUID) -> PlanRecord | None:
        """billing_svc.handle_payment_event 가 subscription.plan_id 로 period_cd 를 알아야 함."""
        ...

    async def create_plan(
        self, plan_key: str, name: str, price_amt: int, period_cd: str
    ) -> PlanRecord:
        """admin/seed 경로 — app 롤은 plan INSERT 권한 없음 (참조테이블)."""
        ...

    async def create_subscription(
        self, learner_id: UUID, plan_id: UUID, status_cd: str, pg_ref: str | None
    ) -> SubscriptionRecord: ...

    async def get_current_subscription(self, learner_id: UUID) -> SubscriptionRecord | None:
        """created_ts 최신 1건."""
        ...

    async def get_subscription_by_pg_ref(self, pg_ref: str) -> SubscriptionRecord | None: ...

    async def set_subscription_status(
        self,
        subscription_id: UUID,
        status_cd: str,
        *,
        started_ts: datetime | None = None,
        current_period_end_ts: datetime | None = None,
    ) -> None: ...

    async def add_payment(
        self,
        learner_id: UUID,
        subscription_id: UUID | None,
        amount_amt: int,
        status_cd: str,
        pg_tx_ref: str | None,
        paid_ts: datetime | None,
    ) -> PaymentRecord: ...

    async def get_payment_by_pg_tx(self, pg_tx_ref: str) -> PaymentRecord | None:
        """웹훅 멱등키 (pg_tx_ref UNIQUE)."""
        ...


class Uow(Protocol):
    """트랜잭션 경계 — 컨텍스트 종료 시 커밋 (engine.begin() 담당)."""

    learning: LearningRepo
    call: CallRepo
    account: AccountRepo
    billing: BillingRepo


class UowFactory(Protocol):
    def learner(self, learner_id: UUID) -> AbstractAsyncContextManager[Uow]: ...

    def admin(self) -> AbstractAsyncContextManager[Uow]: ...


class CatalogPort(Protocol):
    """pattern_key -> PatternSpec (실 카탈로그 테이블은 나중)."""

    async def get_spec(self, pattern_key: str) -> PatternSpec: ...

    async def new_pool(self, have_keys: Sequence[str]) -> list[str]:
        """아직 안 배운 도입후보 key — 우선순위순."""
        ...

    async def starter_pool(self, level_cd: str, count: int) -> list[str]:
        """온보딩 시 레벨별 초기 문형 후보 key — 최대 count 개, 우선순위순."""
        ...
