"""Public domain errors; missing and unauthorized resources share one error."""

from uuid import UUID


class AssessmentError(ValueError):
    pass


class AssessmentForbidden(AssessmentError):
    pass


class AssessmentNotFound(AssessmentError):
    def __init__(self, message: str = "天赋评测资源不存在") -> None:
        super().__init__(message)


class AssessmentConflict(AssessmentError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "esports_operation_conflict",
        active_session_id: UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.active_session_id = active_session_id


class AssessmentExpired(AssessmentError):
    code = "esports_expired"

    def __init__(self, message: str = "天赋评测提交已过期") -> None:
        super().__init__(message)


class AssessmentUnavailable(AssessmentConflict):
    def __init__(self, message: str = "该专项尚未开放") -> None:
        super().__init__(message, code="esports_suite_unavailable")


class AssessmentRateLimited(AssessmentError):
    code = "esports_rate_limited"

    def __init__(self, retry_after: int) -> None:
        if type(retry_after) is not int or retry_after < 1:
            raise ValueError("retry_after must be a positive integer")
        self.retry_after = retry_after
        super().__init__("操作过于频繁，请稍后重试")
