"""설정 — 전부 환경변수. 키/시크릿은 서버에만 존재한다 (중앙화 원칙 3)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAYDAY_", env_file=".env", extra="ignore")

    # DSN 분리 (링크로어 패턴): app=RLS 적용, admin=BYPASSRLS (마이그레이션·carrot·worker)
    db_dsn_app: str = "postgresql+asyncpg://sayday_app:sayday_app@localhost:5432/sayday"
    db_dsn_admin: str = "postgresql+asyncpg://sayday_admin:sayday_admin@localhost:5432/sayday"

    jwt_secret: str = "dev-only-change-me"
    jwt_issuer: str = "sayday"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    env: str = "dev"  # dev | test | prod


@lru_cache
def settings() -> Settings:
    return Settings()
