"""auth 스키마 — 인증만. PII 없음 (ARCHITECTURE §3).

- login_key 는 로그인 식별자(이메일/전화/OAuth sub)로만 존재 — 프로필 아님
- auth 테이블은 app_user 정책이 아예 없다 = RLS fail-closed 로 앱 DSN에서 접근 불가.
  로그인/토큰 갱신은 admin DSN 경로(auth svc)에서만 수행.
- 자격증명 해시는 credential 에만. 탈퇴 시 credential 은 예외적으로 하드 파기.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin


class Identity(Base, IdMixin, TimestampMixin):
    """로그인 주체 1개. 사람(learner)이 아니라 '로그인 수단의 주인'."""

    __tablename__ = "identity"
    __table_args__ = (
        Index("ix_identity_login", "login_kind_cd", "login_key", unique=True),
        {"schema": "auth"},
    )

    login_kind_cd: Mapped[str] = mapped_column(String(20))  # EMAIL/PHONE/APPLE/GOOGLE
    login_key: Mapped[str] = mapped_column(String(320))     # 이메일/전화/oauth sub
    status_cd: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE/LOCKED


class Credential(Base, IdMixin, TimestampMixin):
    __tablename__ = "credential"
    __table_args__ = ({"schema": "auth"},)

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.identity.id")
    )
    password_hash: Mapped[str | None] = mapped_column(String(300))  # OAuth 는 None


class RefreshToken(Base, IdMixin, TimestampMixin):
    __tablename__ = "refresh_token"
    __table_args__ = ({"schema": "auth"},)

    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.identity.id")
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    aud_cd: Mapped[str] = mapped_column(String(20))  # APP/CARROT
    expires_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    revoked_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Otp(Base, IdMixin, TimestampMixin):
    __tablename__ = "otp"
    __table_args__ = ({"schema": "auth"},)

    login_key: Mapped[str] = mapped_column(String(320))
    code_hash: Mapped[str] = mapped_column(String(128))
    purpose_cd: Mapped[str] = mapped_column(String(20))  # SIGNUP/LOGIN/RESET
    expires_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    used_yn: Mapped[bool] = mapped_column(default=False)
