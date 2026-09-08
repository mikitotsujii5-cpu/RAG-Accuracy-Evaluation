"""Safe exceptions that may be returned to the browser."""

from __future__ import annotations

from typing import Any


class AppError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


class ResourceNotReadyError(AppError):
    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        details = {"missing": missing} if missing else None
        super().__init__(
            "RESOURCE_NOT_READY",
            message,
            status_code=503,
            retryable=True,
            details=details,
        )


class ForbiddenError(AppError):
    def __init__(self, message: str = "この操作を行う権限がありません。") -> None:
        super().__init__("FORBIDDEN", message, status_code=403)


class NotFoundError(AppError):
    def __init__(self, message: str = "対象が見つかりません。") -> None:
        super().__init__("NOT_FOUND", message, status_code=404)
