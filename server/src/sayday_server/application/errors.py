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


class InvalidStateError(AppError):
    """상태머신이 허용하지 않는 전이 — ring 수명주기 위반 등 (§5 RING_)."""

    def __init__(
        self, message: str = "허용되지 않은 상태 전이입니다", *, error_code: str = "RING_001"
    ) -> None:
        super().__init__(error_code, message, http_status=409)


class NotFoundError(AppError):
    """대상 리소스 없음. error_code 는 호출부가 도메인 프리픽스로 지정(§5)."""

    def __init__(
        self, message: str = "대상을 찾을 수 없습니다", *, error_code: str = "NOT_FOUND"
    ) -> None:
        super().__init__(error_code, message, http_status=404)
