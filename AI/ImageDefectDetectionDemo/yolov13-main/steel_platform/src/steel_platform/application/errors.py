from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__("not_found", message, status_code=404)


class RevisionConflictError(ApplicationError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            "revision_conflict",
            "该条目已被更新，请刷新后重新复核",
            status_code=409,
            details={"expected_revision": expected, "actual_revision": actual},
        )

