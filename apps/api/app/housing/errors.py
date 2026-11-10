from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from app.auth.contact_readiness import VerifiedEmailRequired

if TYPE_CHECKING:
    from app.housing.reporting_types import ProjectionIssue


class HousingServiceError(ValueError):
    code = "housing_service_error"


class HousingVerifiedEmailRequired(HousingServiceError, VerifiedEmailRequired):
    code = "verified_email_required"


class HousingPersistenceFailure(HousingServiceError):
    code = "housing_persistence_failure"


class HousingSensitiveDataUnavailable(HousingServiceError):
    code = "decrypt_failed"


class HousingResourceNotFound(HousingServiceError):
    code = "housing_resource_not_found"


class HousingDuplicateCatalogEntry(HousingServiceError):
    code = "housing_duplicate_catalog_entry"


class HousingParentInactive(HousingServiceError):
    code = "housing_parent_inactive"


class HousingCatalogHasChildren(HousingServiceError):
    code = "housing_catalog_has_children"


class HousingInvalidRoomConfiguration(HousingServiceError):
    code = "housing_invalid_room_configuration"


class HousingIssueWorkflowRequired(HousingServiceError):
    code = "housing_issue_workflow_required"


class HousingStaleVersion(HousingServiceError):
    code = "housing_stale_version"


class HousingBatchConflict(HousingServiceError):
    code = "housing_batch_conflict"

    def __init__(self, errors: Sequence[dict[str, str]]) -> None:
        super().__init__(self.code)
        self.errors = [dict(error) for error in errors]


class HousingRoomPendingCleaning(HousingServiceError):
    code = "housing_room_pending_cleaning"


class HousingVoidOperationConflict(HousingServiceError):
    code = "housing_void_operation_conflict"


class HousingStayAlreadyVoided(HousingServiceError):
    code = "housing_stay_already_voided"


class HousingVoidReasonInvalid(HousingServiceError):
    code = "housing_void_reason_invalid"


class HousingAccountRequired(HousingServiceError):
    code = "housing_account_required"


class HousingProfileIncomplete(HousingServiceError):
    code = "housing_profile_incomplete"


class MinorGuardianRequired(HousingServiceError):
    code = "minor_guardian_required"


class HousingRoomNotVerified(HousingServiceError):
    code = "housing_room_not_verified"


class HousingRoomUnavailable(HousingServiceError):
    code = "housing_room_unavailable"


class HousingCapacityConflict(HousingServiceError):
    code = "housing_capacity_conflict"


class HousingGenderConflict(HousingServiceError):
    code = "housing_gender_conflict"


class HousingPersonTimeConflict(HousingServiceError):
    code = "housing_person_time_conflict"


class HousingCheckoutConfirmationRequired(HousingServiceError):
    code = "housing_checkout_confirmation_required"


class HousingRoomHasActiveStays(HousingServiceError):
    code = "housing_room_has_active_stays"


class HousingInvalidStayTransition(HousingServiceError):
    code = "housing_invalid_stay_transition"


class HousingOpenIssueExists(HousingServiceError):
    code = "housing_open_issue_exists"


class HousingIssueNotOpen(HousingServiceError):
    code = "housing_issue_not_open"


class HousingReportingError(HousingServiceError):
    code = "housing_service_failure"


class _HousingReportGenerationFailure(HousingReportingError):
    def __init__(
        self,
        batch_id: UUID,
        failure_summary: dict[str, object],
        issues: Sequence[ProjectionIssue],
    ) -> None:
        super().__init__(self.code)
        self.batch_id = batch_id
        self.failure_summary = dict(failure_summary)
        self.issues = tuple(issues)


class HousingReportEmpty(_HousingReportGenerationFailure):
    code = "housing_report_empty"


class HousingExportIncomplete(_HousingReportGenerationFailure):
    code = "housing_export_incomplete"


class HousingReportScopeDenied(HousingReportingError):
    code = "housing_report_scope_denied"


class HousingReportStateConflict(HousingReportingError):
    code = "housing_report_state_conflict"


class HousingReportCorrectionInvalid(HousingReportingError):
    code = "housing_report_correction_invalid"


class HousingReportSnapshotUnavailable(HousingReportingError):
    code = "housing_report_snapshot_unavailable"


class HousingReportRenderFailed(HousingReportingError):
    code = "housing_report_render_failed"


class HousingAuditFailure(HousingReportingError):
    code = "housing_audit_failed"
