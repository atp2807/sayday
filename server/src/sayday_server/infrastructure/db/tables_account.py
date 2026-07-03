"""account 스키마 — 사람. 자격증명 없음 (ARCHITECTURE §3).

- learner.identity_id → auth.identity.id 단방향 참조 (auth 는 account 를 모름)
- RLS: learner 소유 행만 보인다 (app.current_learner_id GUC) — rls.py 가 정책 SSOT
- 탈퇴 = 익명화(soft): nickname→'탈퇴회원', status_cd→INACTIVE, deleted_ts 기록
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin


class Learner(Base, IdMixin, TimestampMixin):
    __tablename__ = "learner"
    __table_args__ = ({"schema": "account"},)

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.identity.id"), unique=True
    )
    nickname: Mapped[str] = mapped_column(String(50))
    level_cd: Mapped[str | None] = mapped_column(String(20))  # 진단 전 None
    locale_cd: Mapped[str] = mapped_column(String(10), default="ko")
    tz_name: Mapped[str] = mapped_column(String(50), default="Asia/Seoul")
    status_cd: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE/SUSPENDED/INACTIVE
    deleted_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Device(Base, IdMixin, TimestampMixin):
    __tablename__ = "device"
    __table_args__ = ({"schema": "account"},)

    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.learner.id")
    )
    platform_cd: Mapped[str] = mapped_column(String(10))  # IOS/ANDROID
    device_key: Mapped[str] = mapped_column(String(200))
    last_seen_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class PushToken(Base, IdMixin, TimestampMixin):
    __tablename__ = "push_token"
    __table_args__ = ({"schema": "account"},)

    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.learner.id")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.device.id")
    )
    kind_cd: Mapped[str] = mapped_column(String(10))  # VOIP/ALERT
    token: Mapped[str] = mapped_column(String(500))
    revoked_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Consent(Base, IdMixin, TimestampMixin):
    __tablename__ = "consent"
    __table_args__ = ({"schema": "account"},)

    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.learner.id")
    )
    kind_cd: Mapped[str] = mapped_column(String(20))  # TOS/PRIVACY/MARKETING
    agreed_yn: Mapped[bool] = mapped_column(default=False)
    agreed_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
