from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.errors import (
    AdmissionCaseNotReady,
    AdmissionClassNotFound,
    ArchivedCohortLocked,
    CapacityOverrideReasonRequired,
    ClassCapacityReached,
    ClassCohortMismatch,
    ClassInactive,
    EnrollmentChanged,
    EnrollmentNotActive,
    EnrollmentNotFound,
    EnrollmentScheduleInvalid,
)
from app.admissions.models import AdmissionCase, Enrollment, EnrollmentEvent
from app.admissions.schedule import effective_schedule, validate_schedule_values
from app.admissions.schemas import (
    CancelEnrollment,
    EnrollmentScheduleUpdate,
    TransferEnrollment,
)
from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.teaching.models import Cohort, TeachingClass


def complete_active_enrollments_for_cohort(
    db: Session,
    *,
    cohort_id: UUID,
    occurred_at: datetime,
    actor_user_id: UUID | None,
    request_id: str,
) -> int:
    from app.evaluations.survey_snapshot_service import CampSurveySnapshotService

    list(
        db.scalars(
            select(TeachingClass)
            .where(TeachingClass.cohort_id == cohort_id)
            .order_by(TeachingClass.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    enrollments = list(
        db.scalars(
            select(Enrollment)
            .where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status == "active",
            )
            .order_by(Enrollment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for enrollment in enrollments:
        enrollment.status = "completed"
        enrollment.completed_at = occurred_at
        enrollment.updated_by_user_id = actor_user_id
        db.add(
            EnrollmentEvent(
                enrollment_id=enrollment.id,
                event_type="completed",
                old_status="active",
                new_status="completed",
                actor_user_id=actor_user_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
        )
    db.flush()
    snapshot_service = CampSurveySnapshotService(db)
    for enrollment in enrollments:
        snapshot_service.ensure_cycle_in_transaction(
            enrollment.id,
            actor_user_id=actor_user_id,
        )
    return len(enrollments)


class EnrollmentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _preloaded_enrollment(self, enrollment_id: UUID) -> Enrollment:
        enrollment = self.db.get(Enrollment, enrollment_id)
        if enrollment is None:
            raise EnrollmentNotFound("enrollment was not found")
        return enrollment

    def _locked_cohort(self, cohort_id: UUID) -> Cohort:
        cohort = self.db.scalar(
            select(Cohort)
            .where(Cohort.id == cohort_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cohort is None:
            raise EnrollmentChanged("enrollment cohort changed")
        return cohort

    def _locked_classes(self, class_ids: set[UUID]) -> dict[UUID, TeachingClass]:
        classes = list(
            self.db.scalars(
                select(TeachingClass)
                .where(TeachingClass.id.in_(sorted(class_ids)))
                .order_by(TeachingClass.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        return {teaching_class.id: teaching_class for teaching_class in classes}

    def _locked_cohort_enrollments(self, cohort_id: UUID) -> list[Enrollment]:
        return list(
            self.db.scalars(
                select(Enrollment)
                .where(Enrollment.cohort_id == cohort_id)
                .order_by(Enrollment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def update_schedule(
        self,
        enrollment_id: UUID,
        input: EnrollmentScheduleUpdate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> Enrollment:
        pre_enrollment = self._preloaded_enrollment(enrollment_id)
        expected_cohort_id = pre_enrollment.cohort_id
        try:
            validate_schedule_values(
                input.schedule_mode,
                input.custom_starts_at,
                input.custom_ends_at,
            )
        except ValueError as exc:
            raise EnrollmentScheduleInvalid(str(exc)) from exc

        with write_transaction(self.db):
            cohort = self._locked_cohort(expected_cohort_id)
            if cohort.status == "archived":
                raise ArchivedCohortLocked("archived cohort admissions cannot be changed")
            enrollment = self.db.scalar(
                select(Enrollment)
                .where(Enrollment.id == enrollment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if enrollment is None:
                raise EnrollmentNotFound("enrollment was not found")
            if enrollment.status != "active":
                raise EnrollmentNotActive("enrollment is not active")
            if enrollment.cohort_id != expected_cohort_id:
                raise EnrollmentChanged("enrollment cohort changed")
            enrollment.schedule_mode = input.schedule_mode
            enrollment.custom_starts_at = (
                input.custom_starts_at if input.schedule_mode == "custom" else None
            )
            enrollment.custom_ends_at = (
                input.custom_ends_at if input.schedule_mode == "custom" else None
            )
            enrollment.updated_by_user_id = actor_user_id
            self.db.flush()
            schedule = effective_schedule(enrollment, cohort)
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.enrollment.schedule.update",
                resource_type="enrollment",
                resource_id=enrollment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    **self._audit_metadata(enrollment, class_id=enrollment.class_id),
                    "schedule_mode": schedule.mode,
                    "effective_starts_at": schedule.starts_at.isoformat(),
                    "effective_ends_at": schedule.ends_at.isoformat(),
                    **(
                        {
                            "custom_starts_at": enrollment.custom_starts_at.isoformat(),
                            "custom_ends_at": enrollment.custom_ends_at.isoformat(),
                        }
                        if enrollment.custom_starts_at is not None
                        and enrollment.custom_ends_at is not None
                        else {}
                    ),
                },
            )
        return enrollment

    @staticmethod
    def _audit_metadata(
        enrollment: Enrollment,
        *,
        class_id: UUID,
    ) -> dict[str, str]:
        return {
            "student_person_id": str(enrollment.student_person_id),
            "admission_case_id": str(enrollment.admission_case_id),
            "enrollment_id": str(enrollment.id),
            "cohort_id": str(enrollment.cohort_id),
            "class_id": str(class_id),
        }

    def transfer(
        self,
        enrollment_id: UUID,
        input: TransferEnrollment,
        *,
        actor_user_id: UUID,
        request_id: str,
        capacity_override_allowed: bool = False,
    ) -> Enrollment:
        pre_enrollment = self._preloaded_enrollment(enrollment_id)
        expected_cohort_id = pre_enrollment.cohort_id
        expected_source_class_id = pre_enrollment.class_id
        if input.target_class_id == expected_source_class_id:
            raise EnrollmentChanged("target class must differ from source class")

        with write_transaction(self.db):
            cohort = self._locked_cohort(expected_cohort_id)
            if cohort.status == "archived":
                raise ArchivedCohortLocked("archived cohort admissions cannot be changed")

            classes = self._locked_classes(
                {expected_source_class_id, input.target_class_id}
            )
            source_class = classes.get(expected_source_class_id)
            if source_class is None:
                raise EnrollmentChanged("enrollment source class changed")
            target_class = classes.get(input.target_class_id)
            if target_class is None:
                raise AdmissionClassNotFound("teaching class was not found")
            if source_class.cohort_id != cohort.id:
                raise EnrollmentChanged("enrollment source class changed cohorts")
            if target_class.cohort_id != cohort.id:
                raise ClassCohortMismatch("target class belongs to another cohort")

            cohort_enrollments = self._locked_cohort_enrollments(cohort.id)
            enrollment = next(
                (item for item in cohort_enrollments if item.id == enrollment_id),
                None,
            )
            if enrollment is None:
                raise EnrollmentChanged("enrollment changed cohorts")
            if enrollment.status != "active":
                raise EnrollmentNotActive("enrollment is not active")
            if enrollment.class_id != expected_source_class_id:
                raise EnrollmentChanged("enrollment class changed during transfer")
            if not target_class.is_active:
                raise ClassInactive("teaching class is inactive")

            target_count = sum(
                item.status == "active" and item.class_id == target_class.id
                for item in cohort_enrollments
            )
            target_is_full = target_count >= target_class.capacity
            capacity_reason = (
                input.capacity_override_reason.strip()
                if input.capacity_override_reason is not None
                else ""
            )
            if target_is_full and not capacity_override_allowed:
                raise ClassCapacityReached("teaching class has reached capacity")
            if target_is_full and not capacity_reason:
                raise CapacityOverrideReasonRequired(
                    "capacity override requires a reason"
                )

            enrollment.class_id = target_class.id
            enrollment.capacity_override_reason = (
                capacity_reason if target_is_full else None
            )
            enrollment.updated_by_user_id = actor_user_id
            target_count_after = target_count + 1
            self.db.add(
                EnrollmentEvent(
                    enrollment_id=enrollment.id,
                    event_type="transferred",
                    old_class_id=source_class.id,
                    new_class_id=target_class.id,
                    old_status="active",
                    new_status="active",
                    reason=input.reason,
                    enrollment_count_snapshot=target_count_after,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
            )
            if target_is_full:
                self.db.add(
                    EnrollmentEvent(
                        enrollment_id=enrollment.id,
                        event_type="capacity_overridden",
                        old_class_id=source_class.id,
                        new_class_id=target_class.id,
                        old_status="active",
                        new_status="active",
                        reason=capacity_reason,
                        enrollment_count_snapshot=target_count_after,
                        actor_user_id=actor_user_id,
                        request_id=request_id,
                    )
                )
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.enrollment.transfer",
                resource_type="enrollment",
                resource_id=enrollment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata=self._audit_metadata(enrollment, class_id=target_class.id),
            )
        return enrollment

    def cancel(
        self,
        enrollment_id: UUID,
        input: CancelEnrollment,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> Enrollment:
        pre_enrollment = self._preloaded_enrollment(enrollment_id)
        expected_cohort_id = pre_enrollment.cohort_id
        expected_class_id = pre_enrollment.class_id
        expected_case_id = pre_enrollment.admission_case_id

        with write_transaction(self.db):
            cohort = self._locked_cohort(expected_cohort_id)
            if cohort.status == "archived":
                raise ArchivedCohortLocked("archived cohort admissions cannot be changed")

            classes = self._locked_classes({expected_class_id})
            teaching_class = classes.get(expected_class_id)
            if teaching_class is None or teaching_class.cohort_id != cohort.id:
                raise EnrollmentChanged("enrollment class changed")

            cohort_enrollments = self._locked_cohort_enrollments(cohort.id)
            enrollment = next(
                (item for item in cohort_enrollments if item.id == enrollment_id),
                None,
            )
            if enrollment is None:
                raise EnrollmentChanged("enrollment changed cohorts")
            if enrollment.status != "active":
                raise EnrollmentNotActive("enrollment is not active")
            if (
                enrollment.class_id != expected_class_id
                or enrollment.admission_case_id != expected_case_id
            ):
                raise EnrollmentChanged("enrollment changed during cancellation")

            admission_case = self.db.scalar(
                select(AdmissionCase)
                .where(AdmissionCase.id == expected_case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                admission_case is None
                or admission_case.status != "enrolled"
                or admission_case.current_enrollment_id != enrollment.id
            ):
                raise AdmissionCaseNotReady("admission case is not enrolled")

            occurred_at = self.db.scalar(select(func.now()))
            if occurred_at is None:
                raise RuntimeError("database time was unavailable")
            enrollment.status = "cancelled"
            enrollment.cancelled_at = occurred_at
            enrollment.updated_by_user_id = actor_user_id
            admission_case.status = "ready"
            admission_case.current_enrollment_id = None
            admission_case.agreed_tuition_cents = None
            admission_case.tuition_override_reason = None
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = actor_user_id
            remaining_count = sum(
                item.id != enrollment.id
                and item.status == "active"
                and item.class_id == teaching_class.id
                for item in cohort_enrollments
            )
            self.db.add(
                EnrollmentEvent(
                    enrollment_id=enrollment.id,
                    event_type="cancelled",
                    old_class_id=teaching_class.id,
                    new_class_id=None,
                    old_status="active",
                    new_status="cancelled",
                    reason=input.reason,
                    enrollment_count_snapshot=remaining_count,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
            )
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="admissions.enrollment.cancel",
                resource_type="enrollment",
                resource_id=enrollment.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata=self._audit_metadata(enrollment, class_id=teaching_class.id),
            )
        return enrollment
