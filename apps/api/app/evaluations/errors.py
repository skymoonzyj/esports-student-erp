class EvaluationBridgeError(ValueError):
    code = "evaluation_bridge_error"


class EvaluationBridgeRequestNotFound(EvaluationBridgeError):
    code = "bridge_request_not_found"


class EvaluationBridgeRequestExpired(EvaluationBridgeError):
    code = "bridge_request_expired"


class EvaluationBridgeRequestConflict(EvaluationBridgeError):
    code = "bridge_request_conflict"


class EvaluationTicketInvalid(EvaluationBridgeError):
    code = "bridge_ticket_invalid"


class EvaluationDelegatedSessionInvalid(EvaluationBridgeError):
    code = "delegated_session_invalid"


class EvaluationAccessDenied(EvaluationBridgeError):
    code = "evaluation_access_denied"


class EvaluationStaffServiceError(ValueError):
    code = "evaluation_staff_service_error"


class StaffRoleNotFound(EvaluationStaffServiceError):
    code = "staff_role_not_found"


class StaffRoleInactive(EvaluationStaffServiceError):
    code = "staff_role_inactive"


class StaffAssignmentScopeInvalid(EvaluationStaffServiceError):
    code = "staff_assignment_scope_invalid"


class StaffNotEligible(EvaluationStaffServiceError):
    code = "staff_not_eligible"


class StaffPermissionNotAllowed(EvaluationStaffServiceError):
    code = "staff_permission_not_allowed"


class StaffAssignmentConflict(EvaluationStaffServiceError):
    code = "staff_assignment_conflict"


class StaffAssignmentNotFound(EvaluationStaffServiceError):
    code = "staff_assignment_not_found"


class StaffAssignmentAlreadyEnded(EvaluationStaffServiceError):
    code = "staff_assignment_already_ended"


class EvaluationGameAccountError(ValueError):
    code = "evaluation_game_account_error"


class EvaluationResourceNotFound(EvaluationGameAccountError):
    code = "evaluation_resource_not_found"


class EvaluationOperationDenied(EvaluationGameAccountError):
    code = "evaluation_operation_denied"


class InvalidGameId(EvaluationGameAccountError):
    code = "invalid_game_id"


class GameAccountLimitReached(EvaluationGameAccountError):
    code = "game_account_limit_reached"


class GameAccountLimitInvalid(EvaluationGameAccountError):
    code = "game_account_limit_invalid"


class GameIdAlreadyBound(EvaluationGameAccountError):
    code = "game_id_already_bound"


class GameAccountAlreadyUnbound(EvaluationGameAccountError):
    code = "game_account_already_unbound"


class AuxiliaryPhoneConflict(EvaluationGameAccountError):
    code = "auxiliary_phone_conflict"


class AnalysisProfileError(ValueError):
    code = "analysis_profile_error"


class InvalidAnalysisProfile(AnalysisProfileError):
    code = "invalid_analysis_profile"


class AnalysisAdapterNotAccepted(InvalidAnalysisProfile):
    code = "analysis_adapter_not_accepted"


class AnalysisProfileVersionConflict(AnalysisProfileError):
    code = "analysis_profile_version_conflict"


class EvaluationMediaError(ValueError):
    code = "evaluation_media_error"


class MediaValidationFailed(EvaluationMediaError):
    code = "media_validation_failed"

    def __init__(self, internal_reason: str) -> None:
        self.internal_reason = internal_reason
        super().__init__("media validation failed")


class MediaAssetNotFound(EvaluationMediaError):
    code = "media_asset_not_found"
