from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.errors import (
    AdmissionCaseNotFound,
    AdmissionCaseNotReady,
    AdmissionClassNotFound,
    AdmissionPaymentRequired,
    AdmissionStudentNotFound,
    AdmissionVerifiedContactRequired,
    ArchivedCohortLocked,
    CapacityOverrideReasonRequired,
    ClassCapacityReached,
    ClassCohortMismatch,
    ClassInactive,
    CohortNotOpen,
    DuplicateCohortEnrollment,
    EnrollmentProjectMismatch,
    EnrollmentScheduleInvalid,
    MinorGuardianRequired,
    TuitionOverrideReasonRequired,
)
from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    Payment,
    PaymentRefund,
)
from app.admissions.schedule import validate_schedule_values
from app.admissions.schemas import ConfirmEnrollment
from app.audit.service import AuditService
from app.auth.contact_readiness import VerifiedContactPolicy, VerifiedContactRequired
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.models import StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.revenue_share.service import RevenueShareService
from app.teaching.models import Cohort, TeachingClass


class AdmissionCaseService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_case_context(self, admission_case_id: UUID) -> AdmissionCase:
        enrollment_context = list(
            self.db.execute(
                select(Enrollment.id, Enrollment.cohort_id, Enrollment.class_id).where(
                    Enrollment.admission_case_id == admission_case_id
                )
            )
        )
        cohort_ids = sorted({row.cohort_id for row in enrollment_context})
        cohorts = (
            list(
                self.db.scalars(
                    select(Cohort)
                    .where(Cohort.id.in_(cohort_ids))
                    .order_by(Cohort.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if cohort_ids
            else []
        )
        class_ids = sorted({row.class_id for row in enrollment_context})
        if class_ids:
            list(
                self.db.scalars(
                    select(TeachingClass)
                    .where(TeachingClass.id.in_(class_ids))
                    .order_by(TeachingClass.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        enrollment_ids = sorted(row.id for row in enrollment_context)
        if enrollment_ids:
            list(
                self.db.scalars(
                    select(Enrollment)
                    .where(Enrollment.id.in_(enrollment_ids))
                    .order_by(Enrollment.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        admission_case = self.db.scalar(
            select(AdmissionCase)
            .where(AdmissionCase.id == admission_case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if admission_case is None:
            raise AdmissionCaseNotFound("admission case was not found")
        if any(cohort.status == "archived" for cohort in cohorts):
            raise ArchivedCohortLocked("archived cohort admissions cannot be changed")
        return admission_case

    def match_student(
        self,
        admission_case_id: UUID,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> AdmissionCase:
        with write_transaction(self.db):
            admission_case = self._lock_case_context(admission_case_id)
            if admission_case.status not in {"pending_match", "ready"}:
                raise AdmissionCaseNotReady("admission case cannot be matched")
            policy = GuardianReadinessPolicy(self.db)
            people = policy.lock_students([student_person_id])
            student = people[0] if people else None
            profile = self.db.get(StudentProfile, student_person_id)
            if student is None or student.deleted_at is not None or profile is None:
                raise AdmissionStudentNotFound("student was not found")
            try:
                VerifiedContactPolicy(self.db).require_verified_contact_for_person(student.id)
            except VerifiedContactRequired as exc:
                raise AdmissionVerifiedContactRequired("student requires a verified contact") from exc
            admission_case.student_person_id = student.id
            admission_case.status = "ready"
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = actor_user_id
            self.db.flush()
        return admission_case

    def close(
        self,
        admission_case_id: UUID,
        *,
        reason: str,
        actor_user_id: UUID,
    ) -> AdmissionCase:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("close reason is required")
        with write_transaction(self.db):
            admission_case = self._lock_case_context(admission_case_id)
            if (
                admission_case.status not in {"pending_match", "ready"}
                or admission_case.current_enrollment_id is not None
            ):
                raise AdmissionCaseNotReady("admission case cannot be closed")
            admission_case.status = "closed"
            admission_case.closed_reason = normalized_reason
            admission_case.updated_by_user_id = actor_user_id
            self.db.flush()
        return admission_case

    def reopen(
        self,
        admission_case_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> AdmissionCase:
        with write_transaction(self.db):
            admission_case = self._lock_case_context(admission_case_id)
            if (
                admission_case.status != "closed"
                or admission_case.current_enrollment_id is not None
            ):
                raise AdmissionCaseNotReady("admission case is not closed")
            admission_case.status = (
                "ready" if admission_case.student_person_id is not None else "pending_match"
            )
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = actor_user_id
            self.db.flush()
        return admission_case

    @staticmethod
    def _integrity_error(exc: IntegrityError) -> Exception:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_enrollments_active_student_cohort":
            return DuplicateCohortEnrollment(
                "student already has an active enrollment in this cohort"
            )
        return exc

    def _locked_payments(self, admission_case_id: UUID) -> list[Payment]:
        payment_ids = list(
            self.db.scalars(
                select(AdmissionCasePayment.payment_id)
                .where(AdmissionCasePayment.admission_case_id == admission_case_id)
                .order_by(AdmissionCasePayment.payment_id)
            )
        )
        if not payment_ids:
            return []
        return list(
            self.db.scalars(
                select(Payment)
                .where(Payment.id.in_(payment_ids))
                .order_by(Payment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _net_paid_cents(self, payments: list[Payment]) -> int:
        if not payments:
            return 0
        payment_ids = [payment.id for payment in payments]
        refunded = self.db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount_cents), 0)).where(
                PaymentRefund.payment_id.in_(payment_ids)
            )
        )
        return sum(payment.amount_cents for payment in payments) - int(refunded or 0)

    def confirm_enrollment(
        self,
        admission_case_id: UUID,
        input: ConfirmEnrollment,
        *,
        actor_user_id: UUID,
        request_id: str,
        capacity_override_allowed: bool = False,
    ) -> Enrollment:
        pre_class = self.db.get(TeachingClass, input.class_id)
        if pre_class is None:
            raise AdmissionClassNotFound("teaching class was not found")
        pre_case = self.db.get(AdmissionCase, admission_case_id)
        if pre_case is None:
            raise AdmissionCaseNotFound("admission case was not found")
        cohort_id = pre_class.cohort_id
        expected_student_id = pre_case.student_person_id
        history_context = list(
            self.db.execute(
                select(Enrollment.id, Enrollment.cohort_id, Enrollment.class_id).where(
                    Enrollment.admission_case_id == admission_case_id
                )
            )
        )

        try:
            with write_transaction(self.db):
                cohort_ids = sorted({cohort_id, *(row.cohort_id for row in history_context)})
                locked_cohorts = list(
                    self.db.scalars(
                        select(Cohort)
                        .where(Cohort.id.in_(cohort_ids))
                        .order_by(Cohort.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                cohorts_by_id = {cohort.id: cohort for cohort in locked_cohorts}
                cohort = cohorts_by_id.get(cohort_id)
                if cohort is None:
                    raise ClassCohortMismatch("class cohort was not found")
                try:
                    validate_schedule_values(
                        input.schedule_mode,
                        input.custom_starts_at,
                        input.custom_ends_at,
                    )
                except ValueError as exc:
                    raise EnrollmentScheduleInvalid(str(exc)) from exc
                if any(
                    cohorts_by_id.get(row.cohort_id) is not None
                    and cohorts_by_id[row.cohort_id].status == "archived"
                    for row in history_context
                ):
                    raise ArchivedCohortLocked("archived cohort admissions cannot be changed")

                class_ids = sorted({input.class_id, *(row.class_id for row in history_context)})
                locked_classes = list(
                    self.db.scalars(
                        select(TeachingClass)
                        .where(TeachingClass.id.in_(class_ids))
                        .order_by(TeachingClass.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                classes_by_id = {
                    teaching_class.id: teaching_class for teaching_class in locked_classes
                }
                teaching_class = classes_by_id.get(input.class_id)
                if teaching_class is None:
                    raise AdmissionClassNotFound("teaching class was not found")
                if teaching_class.cohort_id != cohort.id:
                    raise ClassCohortMismatch("class changed cohorts during confirmation")

                active_enrollment_ids = set(
                    self.db.scalars(
                        select(Enrollment.id).where(
                            Enrollment.cohort_id == cohort.id,
                            Enrollment.status == "active",
                        )
                    )
                )
                enrollment_ids = sorted(active_enrollment_ids | {row.id for row in history_context})
                locked_enrollments = (
                    list(
                        self.db.scalars(
                            select(Enrollment)
                            .where(Enrollment.id.in_(enrollment_ids))
                            .order_by(Enrollment.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    )
                    if enrollment_ids
                    else []
                )
                active_enrollments = [
                    enrollment
                    for enrollment in locked_enrollments
                    if enrollment.cohort_id == cohort.id and enrollment.status == "active"
                ]
                admission_case = self.db.scalar(
                    select(AdmissionCase)
                    .where(AdmissionCase.id == admission_case_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if admission_case is None:
                    raise AdmissionCaseNotFound("admission case was not found")
                if (
                    admission_case.status != "ready"
                    or admission_case.student_person_id is None
                    or admission_case.current_enrollment_id is not None
                ):
                    raise AdmissionCaseNotReady("admission case is not ready")
                if admission_case.student_person_id != expected_student_id:
                    raise AdmissionCaseNotReady("admission case student changed")
                if (
                    admission_case.selected_project_id is not None
                    and teaching_class.project_id != admission_case.selected_project_id
                ):
                    raise EnrollmentProjectMismatch(
                        "teaching class does not belong to the selected project"
                    )
                student_id = admission_case.student_person_id

                policy = GuardianReadinessPolicy(self.db)
                people = policy.lock_students([student_id])
                student = people[0] if people else None
                profile = self.db.get(StudentProfile, student_id)
                if student is None or student.deleted_at is not None or profile is None:
                    raise AdmissionStudentNotFound("student was not found")
                try:
                    VerifiedContactPolicy(self.db).require_verified_contact_for_person(student_id)
                except VerifiedContactRequired as exc:
                    raise AdmissionVerifiedContactRequired(
                        "student requires a verified contact"
                    ) from exc
                projection = policy.evaluate_one(
                    student_id,
                    business_date_at(
                        input.custom_starts_at
                        if input.schedule_mode == "custom" and input.custom_starts_at is not None
                        else cohort.starts_at
                    ),
                )
                if projection.age_status == "minor" and projection.guardian_status != "ready":
                    raise MinorGuardianRequired("student needs a valid guardian at cohort start")

                payments = self._locked_payments(admission_case.id)
                if self._net_paid_cents(payments) <= 0:
                    raise AdmissionPaymentRequired("admission requires a net payment")
                if cohort.status != "open":
                    raise CohortNotOpen("cohort is not open for enrollment")
                if not teaching_class.is_active:
                    raise ClassInactive("teaching class is inactive")
                if any(
                    enrollment.student_person_id == student_id for enrollment in active_enrollments
                ):
                    raise DuplicateCohortEnrollment(
                        "student already has an active enrollment in this cohort"
                    )

                class_count = sum(
                    enrollment.class_id == teaching_class.id for enrollment in active_enrollments
                )
                class_is_full = class_count >= teaching_class.capacity
                capacity_reason = (
                    input.capacity_override_reason.strip()
                    if input.capacity_override_reason is not None
                    else ""
                )
                if class_is_full and not capacity_override_allowed:
                    raise ClassCapacityReached("teaching class has reached capacity")
                if class_is_full and not capacity_reason:
                    raise CapacityOverrideReasonRequired("capacity override requires a reason")

                tuition_baseline_cents = (
                    admission_case.agreed_tuition_cents
                    if admission_case.application_source == "self_service"
                    and admission_case.agreed_tuition_cents is not None
                    else teaching_class.default_tuition_cents
                )
                agreed_tuition_cents = (
                    tuition_baseline_cents
                    if input.agreed_tuition_cents is None
                    else input.agreed_tuition_cents
                )
                tuition_changed = agreed_tuition_cents != tuition_baseline_cents
                tuition_reason = (
                    input.tuition_override_reason.strip()
                    if input.tuition_override_reason is not None
                    else ""
                )
                if tuition_changed and not tuition_reason:
                    raise TuitionOverrideReasonRequired("tuition override requires a reason")

                enrollment = Enrollment(
                    admission_case_id=admission_case.id,
                    student_person_id=student_id,
                    cohort_id=cohort.id,
                    class_id=teaching_class.id,
                    agreed_tuition_cents=agreed_tuition_cents,
                    status="active",
                    capacity_override_reason=(capacity_reason if class_is_full else None),
                    schedule_mode=input.schedule_mode,
                    custom_starts_at=(
                        input.custom_starts_at if input.schedule_mode == "custom" else None
                    ),
                    custom_ends_at=(
                        input.custom_ends_at if input.schedule_mode == "custom" else None
                    ),
                    confirmed_at=func.now(),
                    created_by_user_id=actor_user_id,
                )
                self.db.add(enrollment)
                self.db.flush()
                admission_case.agreed_tuition_cents = agreed_tuition_cents
                admission_case.tuition_override_reason = tuition_reason if tuition_changed else None
                admission_case.current_enrollment_id = enrollment.id
                admission_case.status = "enrolled"
                admission_case.updated_by_user_id = actor_user_id
                confirmed_count = class_count + 1
                self.db.add(
                    EnrollmentEvent(
                        enrollment_id=enrollment.id,
                        event_type="confirmed",
                        old_class_id=None,
                        new_class_id=teaching_class.id,
                        old_status=None,
                        new_status="active",
                        reason=None,
                        enrollment_count_snapshot=confirmed_count,
                        actor_user_id=actor_user_id,
                        request_id=request_id,
                    )
                )
                if class_is_full:
                    self.db.add(
                        EnrollmentEvent(
                            enrollment_id=enrollment.id,
                            event_type="capacity_overridden",
                            old_class_id=None,
                            new_class_id=teaching_class.id,
                            old_status=None,
                            new_status="active",
                            reason=capacity_reason,
                            enrollment_count_snapshot=confirmed_count,
                            actor_user_id=actor_user_id,
                            request_id=request_id,
                        )
                    )
                self.db.flush()
                payment_ids = list(
                    self.db.scalars(
                        select(AdmissionCasePayment.payment_id).where(
                            AdmissionCasePayment.admission_case_id == admission_case.id
                        )
                    )
                )
                for payment_id in payment_ids:
                    RevenueShareService(self.db).reconcile_payment_in_transaction(payment_id)
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="admissions.enrollment.confirm",
                    resource_type="enrollment",
                    resource_id=enrollment.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "student_person_id": str(student_id),
                        "admission_case_id": str(admission_case.id),
                        "enrollment_id": str(enrollment.id),
                        "cohort_id": str(cohort.id),
                        "class_id": str(teaching_class.id),
                        "schedule_mode": input.schedule_mode,
                        **(
                            {
                                "custom_starts_at": input.custom_starts_at.isoformat(),
                                "custom_ends_at": input.custom_ends_at.isoformat(),
                            }
                            if input.custom_starts_at is not None
                            and input.custom_ends_at is not None
                            else {}
                        ),
                    },
                )
            return enrollment
        except IntegrityError as exc:
            translated = self._integrity_error(exc)
            if translated is exc:
                raise
            raise translated from exc
