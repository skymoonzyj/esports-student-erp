from __future__ import annotations

from uuid import UUID


class AdmissionsServiceError(ValueError):
    code = "admissions_service_error"


class ServiceAccountNotFound(AdmissionsServiceError):
    code = "service_account_not_found"


class ServiceCredentialNotFound(AdmissionsServiceError):
    code = "service_credential_not_found"


class PaymentSignatureInvalid(AdmissionsServiceError):
    code = "payment_signature_invalid"


class PaymentRequestExpired(AdmissionsServiceError):
    code = "payment_request_expired"


class PaymentReplayRejected(AdmissionsServiceError):
    code = "payment_replay_rejected"


class PaymentRateLimited(AdmissionsServiceError):
    code = "payment_rate_limited"


class ServiceAccountDisabled(AdmissionsServiceError):
    code = "service_account_disabled"


class ServiceCredentialDisabled(AdmissionsServiceError):
    code = "service_credential_disabled"


class PaymentSourceNotAllowed(AdmissionsServiceError):
    code = "payment_source_not_allowed"


class PaymentImportConflict(AdmissionsServiceError):
    code = "payment_import_conflict"

    def __init__(self, conflict_id: UUID) -> None:
        self.conflict_id = conflict_id
        super().__init__("payment import conflicts with an existing request")


class AdmissionCaseNotFound(AdmissionsServiceError):
    code = "admission_case_not_found"


class AdmissionCaseClosed(AdmissionsServiceError):
    code = "admission_case_closed"


class AdmissionStudentNotFound(AdmissionsServiceError):
    code = "admission_student_not_found"


class PaymentNotFound(AdmissionsServiceError):
    code = "payment_not_found"


class RefundExceedsPaidAmount(AdmissionsServiceError):
    code = "refund_exceeds_paid_amount"


class ArchivedCohortLocked(AdmissionsServiceError):
    code = "archived_cohort_locked"


class AdmissionCaseNotReady(AdmissionsServiceError):
    code = "admission_case_not_ready"


class AdmissionClassNotFound(AdmissionsServiceError):
    code = "admission_class_not_found"


class AdmissionPaymentRequired(AdmissionsServiceError):
    code = "admission_payment_required"


class MinorGuardianRequired(AdmissionsServiceError):
    code = "minor_guardian_required"


class DuplicateCohortEnrollment(AdmissionsServiceError):
    code = "duplicate_cohort_enrollment"


class ClassCapacityReached(AdmissionsServiceError):
    code = "class_capacity_reached"


class CapacityOverrideReasonRequired(AdmissionsServiceError):
    code = "capacity_override_reason_required"


class TuitionOverrideReasonRequired(AdmissionsServiceError):
    code = "tuition_override_reason_required"


class CohortNotOpen(AdmissionsServiceError):
    code = "cohort_not_open"


class ClassInactive(AdmissionsServiceError):
    code = "class_inactive"


class ClassCohortMismatch(AdmissionsServiceError):
    code = "class_cohort_mismatch"


class EnrollmentNotFound(AdmissionsServiceError):
    code = "enrollment_not_found"


class EnrollmentNotActive(AdmissionsServiceError):
    code = "enrollment_not_active"


class EnrollmentChanged(AdmissionsServiceError):
    code = "enrollment_changed"


class WithdrawalRefundChanged(AdmissionsServiceError):
    code = "withdrawal_refund_changed"


class WithdrawalNothingToRefund(AdmissionsServiceError):
    code = "withdrawal_nothing_to_refund"


class DuplicateServiceAccount(AdmissionsServiceError):
    code = "duplicate_service_account"


class PaymentConflictNotFound(AdmissionsServiceError):
    code = "payment_conflict_not_found"


class PaymentConflictNotOpen(AdmissionsServiceError):
    code = "payment_conflict_not_open"
