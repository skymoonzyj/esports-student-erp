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


class EvaluationTaskError(ValueError):
    code = "evaluation_task_error"


class EvaluationUploadLimitReached(EvaluationTaskError):
    code = "evaluation_upload_limit_reached"


class EvaluationIdempotencyConflict(EvaluationTaskError):
    code = "evaluation_idempotency_conflict"


class EvaluationProcessingError(EvaluationTaskError):
    code = "evaluation_processing_error"


class EvaluationLeaseInvalid(EvaluationProcessingError):
    code = "evaluation_lease_invalid"


class EvaluationAdapterNotSupported(EvaluationProcessingError):
    code = "evaluation_adapter_not_supported"


class EvaluationResultInvalid(EvaluationProcessingError):
    code = "evaluation_result_invalid"


class EvaluationTaskVersionConflict(EvaluationTaskError):
    code = "evaluation_stale_version"


class EvaluationTaskStateConflict(EvaluationTaskError):
    code = "evaluation_invalid_transition"


class EvaluationSelfReviewForbidden(EvaluationTaskError):
    code = "evaluation_review_conflict"


class EvaluationAdapterCertificationConflict(EvaluationTaskError):
    code = "evaluation_adapter_certification_conflict"


class TeachingReportError(ValueError):
    code = "teaching_report_error"


class TeachingReportWindowClosed(TeachingReportError):
    code = "teaching_report_window_closed"


class TeachingReportStateConflict(TeachingReportError):
    code = "teaching_report_state_conflict"


class TeachingReportDraftVersionConflict(TeachingReportError):
    code = "teaching_report_draft_stale"


class CampSurveyError(ValueError):
    """Base error for immutable camp completion surveys."""

    code = "camp_survey_error"


class CampSurveyResourceNotFound(CampSurveyError):
    code = "evaluation_resource_not_found"


class CampSurveyWindowClosed(CampSurveyError):
    code = "evaluation_survey_window_closed"


class CampSurveySnapshotConflict(CampSurveyError):
    code = "evaluation_stale_version"


class CampSurveySubmissionConflict(CampSurveyError):
    code = "evaluation_survey_submission_conflict"


class CampSurveyRevisionConflict(CampSurveyError):
    code = "evaluation_survey_revision_conflict"


class CampSurveyValidationError(CampSurveyError):
    code = "evaluation_survey_validation_error"


class CampSurveySupplementConflict(CampSurveyError):
    code = "evaluation_survey_supplement_conflict"
