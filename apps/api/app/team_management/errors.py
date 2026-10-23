from __future__ import annotations


class TeamServiceError(ValueError):
    code = "team_service_error"
    status = 400
    detail = "队伍操作失败"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)


class TeamAccessDenied(TeamServiceError):
    code = "access_denied"
    status = 403
    detail = "无查看权限"


class TeamNotFound(TeamServiceError):
    code = "team_not_found"
    status = 404
    detail = "队伍不存在或无查看权限"


class TeamMemberNotFound(TeamNotFound):
    pass


class TeamAssignmentNotFound(TeamNotFound):
    pass


class TeamOperationConflict(TeamServiceError):
    code = "team_operation_conflict"
    status = 409
    detail = "操作编号已用于不同请求"


class TeamMemberConflict(TeamServiceError):
    code = "team_already_assigned"
    status = 409
    detail = "该人员已有当前队伍"


class TeamInactive(TeamServiceError):
    code = "team_inactive"
    status = 409
    detail = "队伍当前不可加入或分配"


class TeamCandidateIneligible(TeamServiceError):
    code = "team_member_ineligible"
    status = 409
    detail = "该人员当前不符合队员条件"


class TeamSameTeam(TeamServiceError):
    code = "team_same_team"
    status = 409
    detail = "不能调往当前队伍"


class TeamMembershipStale(TeamServiceError):
    code = "team_membership_stale"
    status = 409
    detail = "队员关系已变化，请刷新后重试"


class TeamAssignmentConflict(TeamServiceError):
    code = "team_already_assigned"
    status = 409
    detail = "该管理人员已绑定此队伍"


class TeamAssignmentStale(TeamServiceError):
    code = "team_assignment_stale"
    status = 409
    detail = "管理绑定已变化，请刷新后重试"


class TeamHasActiveRelationships(TeamServiceError):
    code = "team_has_active_relationships"
    status = 409
    detail = "队伍仍有当前成员或管理绑定"
