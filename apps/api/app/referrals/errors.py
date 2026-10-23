from app.core.errors import ProblemError


class InvitedHousingPermissionDenied(ProblemError):
    def __init__(self) -> None:
        super().__init__(
            status=403, code="invited_housing_permission_denied", detail="无权查看邀请住宿"
        )


class InvitationSourceNotFound(ProblemError):
    def __init__(self) -> None:
        super().__init__(status=404, code="invitation_source_not_found", detail="邀请来源不存在")


class InvitedHousingInconsistent(ProblemError):
    def __init__(self) -> None:
        super().__init__(
            status=409, code="invited_housing_inconsistent", detail="住宿状态异常，请联系管理员"
        )
