from app.core.errors import ProblemError


class ProfileClaimError(ProblemError):
    """Safe public error, containing no submitted or matched identity facts."""

    def __init__(
        self,
        code: str = "profile_claim_conflict",
        *,
        status: int = 409,
        detail: str = "申请无法处理，请重试或联系管理员",
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(
            status=status, code=code, detail=detail, retry_after_seconds=retry_after_seconds
        )
