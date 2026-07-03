"""authz(토큰) + 미들웨어 체인(인증·역할가드·에러 envelope) 검증."""
import uuid

import pytest
from fastapi.testclient import TestClient

from sayday_server.application.authz import (
    Principal,
    Role,
    issue_access_token,
    verify_access_token,
)
from sayday_server.application.errors import UnauthenticatedError
from sayday_server.config import Settings
from sayday_server.presentation.http.app import create_app

CFG = Settings(env="test", jwt_secret="test-secret")
IDENTITY = uuid.uuid4()
LEARNER = uuid.uuid4()


def _token(role: Role, learner_id=None) -> str:
    return issue_access_token(CFG, IDENTITY, role, learner_id=learner_id)


# ── authz 단위 ──

def test_token_roundtrip():
    p = verify_access_token(CFG, _token(Role.LEARNER, LEARNER))
    assert p == Principal(identity_id=IDENTITY, role=Role.LEARNER, learner_id=LEARNER)


def test_tampered_token_rejected():
    with pytest.raises(UnauthenticatedError):
        verify_access_token(CFG, _token(Role.LEARNER) + "x")


def test_wrong_secret_rejected():
    other = Settings(env="test", jwt_secret="other-secret")
    with pytest.raises(UnauthenticatedError):
        verify_access_token(CFG, issue_access_token(other, IDENTITY, Role.LEARNER))


# ── 미들웨어 체인 ──

@pytest.fixture
def http():
    return TestClient(create_app(CFG), raise_server_exceptions=False)


def test_health_is_public(http):
    r = http.get("/api/public/health")
    assert r.status_code == 200 and r.json()["alive_yn"] is True


def test_protected_route_requires_token(http):
    r = http.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"error_code": "AUTH_001", "message": r.json()["message"]}


def test_learner_token_passes_and_me_echoes_principal(http):
    r = http.get("/api/me", headers={"authorization": f"Bearer {_token(Role.LEARNER, LEARNER)}"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "LEARNER" and body["learner_id"] == str(LEARNER)


def test_learner_cannot_reach_carrot_route(http):
    r = http.get("/api/carrot/ping", headers={"authorization": f"Bearer {_token(Role.LEARNER)}"})
    assert r.status_code == 403 and r.json()["error_code"] == "AUTH_002"


def test_carrot_token_reaches_carrot_route(http):
    r = http.get("/api/carrot/ping", headers={"authorization": f"Bearer {_token(Role.CARROT)}"})
    assert r.status_code == 200


def test_carrot_token_cannot_use_learner_route(http):
    # aud 분리: carrot 토큰으로 learner 리소스 접근 불가
    r = http.get("/api/me", headers={"authorization": f"Bearer {_token(Role.CARROT)}"})
    assert r.status_code == 403


def test_request_id_header_set(http):
    r = http.get("/api/public/health")
    assert r.headers.get("x-request-id")
