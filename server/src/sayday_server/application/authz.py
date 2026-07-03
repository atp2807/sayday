"""authz — 역할·토큰·권한 검사 단일 지점 (ARCHITECTURE §2).

- 역할: LEARNER / CARROT(운영) / POTATO(개발) / RINGER(내부 시스템). ADMIN 단어 금지.
- JWT aud 가 역할과 1:1 — aud 불일치 = 즉시 거부.
- 권한 체크를 라우터/서비스에 흩뿌리지 않는다. 여기 함수만 사용.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

import jwt

from ..config import Settings
from .errors import ForbiddenError, UnauthenticatedError


class Role(str, Enum):
    LEARNER = "LEARNER"
    CARROT = "CARROT"
    POTATO = "POTATO"
    RINGER = "RINGER"


_AUD: dict[Role, str] = {
    Role.LEARNER: "app",
    Role.CARROT: "carrot",
    Role.POTATO: "potato",
    Role.RINGER: "internal",
}


@dataclass(frozen=True)
class Principal:
    identity_id: uuid.UUID
    role: Role
    learner_id: uuid.UUID | None  # LEARNER 만 보유


def issue_access_token(
    cfg: Settings,
    identity_id: uuid.UUID,
    role: Role,
    learner_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": cfg.jwt_issuer,
        "aud": _AUD[role],
        "sub": str(identity_id),
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=cfg.access_token_minutes)).timestamp()),
    }
    if learner_id is not None:
        payload["learner_id"] = str(learner_id)
    return jwt.encode(payload, cfg.jwt_secret, algorithm="HS256")


def verify_access_token(cfg: Settings, token: str) -> Principal:
    """서명·만료·발급자 검증 후 Principal. aud 는 payload role 과의 정합까지 확인."""
    try:
        payload = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=["HS256"],
            issuer=cfg.jwt_issuer,
            audience=list(_AUD.values()),
        )
        role = Role(payload["role"])
        if payload["aud"] != _AUD[role]:
            raise UnauthenticatedError("토큰 대상(aud)이 올바르지 않습니다")
        learner_raw = payload.get("learner_id")
        return Principal(
            identity_id=uuid.UUID(payload["sub"]),
            role=role,
            learner_id=uuid.UUID(learner_raw) if learner_raw else None,
        )
    except UnauthenticatedError:
        raise
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthenticatedError("토큰이 유효하지 않습니다") from exc


def require_role(principal: Principal, *allowed: Role) -> None:
    if principal.role not in allowed:
        raise ForbiddenError()
