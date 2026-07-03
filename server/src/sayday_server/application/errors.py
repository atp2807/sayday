"""도메인 에러 → API 에러 규격 {error_code: "DOMAIN_NNN", message} (naming 사전)."""
from __future__ import annotations


class AppError(Exception):
    """서비스 레이어가 던지는 유일한 에러 타입. presentation 이 envelope 로 변환."""

    def __init__(self, error_code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status


class UnauthenticatedError(AppError):
    def __init__(self, message: str = "인증이 필요합니다") -> None:
        super().__init__("AUTH_001", message, http_status=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "권한이 없습니다") -> None:
        super().__init__("AUTH_002", message, http_status=403)
