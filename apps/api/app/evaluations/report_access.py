from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, case, or_, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.admissions.schedule import effective_schedule
from app.auth.models import UserAccount
from app.core.time import BUSINESS_TIME_ZONE
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import EvaluationResourceNotFound
from app.evaluations.report_models import StudentReport
from app.evaluations.report_periods import (
    ReportPeriod,
    daily_periods,
    submission_deadline,
    submission_opens_at,
    weekly_periods,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.identity.models import Person, StaffProfile, StudentProfile
from app.rbac.models import Permission
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

_REPORT_WRITE_PERMISSION = "student_reports.write"


def live_report_target_exists(*, occurred_at: datetime) -> ColumnElement[bool]:
    effective_start_at = case(
        (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
        else_=Cohort.starts_at,
    )
    return (
        select(Enrollment.id)
        .join(Cohort, Cohort.id == Enrollment.cohort_id)
        .join(
            TeachingClass,
            (TeachingClass.id == Enrollment.class_id)
            & (TeachingClass.cohort_id == Enrollment.cohort_id),
        )
        .join(GameProject, GameProject.id == TeachingClass.project_id)
        .join(
            StudentProfile,
            StudentProfile.person_id == Enrollment.student_person_id,
        )
        .join(Person, Person.id == Enrollment.student_person_id)
        .where(
            Enrollment.id == StudentReport.enrollment_id,
            Enrollment.student_person_id == StudentReport.student_person_id,
            Enrollment.cohort_id == StudentReport.cohort_id,
            Enrollment.class_id == StudentReport.class_id,
            Enrollment.status.in_(("active", "completed")),
            effective_start_at <= occurred_at,
            Cohort.id == StudentReport.cohort_id,
            Cohort.status.in_(("active", "completed")),
            TeachingClass.id == StudentReport.class_id,
            TeachingClass.cohort_id == StudentReport.cohort_id,
            TeachingClass.project_id == StudentReport.project_id,
            TeachingClass.is_active.is_(True),
            GameProject.id == StudentReport.project_id,
            GameProject.is_active.is_(True),
            StudentProfile.person_id == StudentReport.student_person_id,
            StudentProfile.status == "active",
            Person.id == StudentReport.student_person_id,
            Person.deleted_at.is_(None),
        )
        .correlate(StudentReport)
        .exists()
    )


@dataclass(frozen=True)
class TeachingReportTarget:
    enrollment_id: UUID
    student_person_id: UUID
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: datetime
    cohort_ends_at: datetime
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str

    @property
    def resource(self) -> EvaluationResource:
        return EvaluationResource(
            student_person_id=self.student_person_id,
            enrollment_id=self.enrollment_id,
            cohort_id=self.cohort_id,
            project_id=self.project_id,
            class_id=self.class_id,
        )


@dataclass(frozen=True)
class TeachingReportAuthor:
    person_id: UUID
    user_id: UUID
    source: str
    coach_assignment_id: UUID | None
    staff_assignment_id: UUID | None
    role_key: str
    role_name: str
    role_category: str
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True)
class AuthorizedTeachingReportSlot:
    target: TeachingReportTarget
    author: TeachingReportAuthor
    period: ReportPeriod
    submission_opens_at: datetime
    initial_submission_deadline: datetime
    draft_allowed: bool
    submit_allowed: bool
    report_id: UUID | None = None
    report_status: str | None = None


class TeachingReportAccessService:
    def __init__(
        self,
        db: Session,
        *,
        access: EvaluationAccessService | None = None,
        context: EvaluationContextService | None = None,
    ) -> None:
        self.db = db
        self.access = access or EvaluationAccessService(db)
        self.context = context or EvaluationContextService(db)

    @staticmethod
    def _not_found() -> EvaluationResourceNotFound:
        return EvaluationResourceNotFound("evaluation resource not found")

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def _active_author_account(self, account_id: UUID) -> tuple[UserAccount, Person]:
        row = self.db.execute(
            select(UserAccount, Person)
            .join(Person, Person.id == UserAccount.person_id)
            .join(StaffProfile, StaffProfile.person_id == Person.id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        ).tuples().one_or_none()
        if row is None:
            raise self._not_found()
        return row

    def _targets(
        self,
        enrollment_ids: set[UUID],
        *,
        occurred_at: datetime,
    ) -> tuple[TeachingReportTarget, ...]:
        if not enrollment_ids:
            return ()
        effective_start_at = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
            else_=Cohort.starts_at,
        )
        rows = self.db.execute(
            select(Enrollment, Cohort, TeachingClass, GameProject)
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(
                TeachingClass,
                (TeachingClass.id == Enrollment.class_id)
                & (TeachingClass.cohort_id == Enrollment.cohort_id),
            )
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .join(
                StudentProfile,
                StudentProfile.person_id == Enrollment.student_person_id,
            )
            .join(Person, Person.id == Enrollment.student_person_id)
            .where(
                Enrollment.id.in_(enrollment_ids),
                Enrollment.status.in_(("active", "completed")),
                effective_start_at <= occurred_at,
                Cohort.status.in_(("active", "completed")),
                TeachingClass.is_active.is_(True),
                GameProject.is_active.is_(True),
                StudentProfile.status == "active",
                Person.deleted_at.is_(None),
            )
        ).tuples()
        targets: list[TeachingReportTarget] = []
        for enrollment, cohort, teaching_class, project in rows:
            schedule = effective_schedule(enrollment, cohort)
            targets.append(
                TeachingReportTarget(
                    enrollment_id=enrollment.id,
                    student_person_id=enrollment.student_person_id,
                    cohort_id=cohort.id,
                    cohort_name=cohort.name,
                    cohort_starts_at=schedule.starts_at,
                    cohort_ends_at=schedule.ends_at,
                    project_id=project.id,
                    project_name=project.name,
                    class_id=teaching_class.id,
                    class_name=teaching_class.name,
                )
            )
        return tuple(targets)

    def _coach_assignments_for_classes(
        self,
        *,
        person_id: UUID,
        class_ids: set[UUID],
        occurred_at: datetime,
    ) -> tuple[CoachAssignment, ...]:
        if not class_ids:
            return ()
        return tuple(
            self.db.scalars(
                select(CoachAssignment)
                .where(
                    CoachAssignment.coach_person_id == person_id,
                    CoachAssignment.class_id.in_(class_ids),
                    CoachAssignment.valid_from <= occurred_at,
                    CoachEligibility.eligible_person_predicate(
                        CoachAssignment.coach_person_id
                    ),
                )
                .order_by(
                    CoachAssignment.valid_from,
                    CoachAssignment.id,
                )
            )
        )

    def _staff_assignments_for_targets(
        self,
        *,
        person_id: UUID,
        targets: tuple[TeachingReportTarget, ...],
        occurred_at: datetime,
    ) -> tuple[tuple[CohortStaffAssignment, EvaluationStaffRole], ...]:
        if not targets:
            return ()
        cohort_ids = {target.cohort_id for target in targets}
        class_ids = {target.class_id for target in targets}
        project_ids = {target.project_id for target in targets}
        return tuple(
            self.db.execute(
                select(CohortStaffAssignment, EvaluationStaffRole)
                .join(
                    EvaluationStaffRole,
                    EvaluationStaffRole.id == CohortStaffAssignment.role_id,
                )
                .where(
                    CohortStaffAssignment.staff_person_id == person_id,
                    CohortStaffAssignment.cohort_id.in_(cohort_ids),
                    CohortStaffAssignment.valid_from <= occurred_at,
                    EvaluationStaffRole.is_active.is_(True),
                    or_(
                        CohortStaffAssignment.class_id.in_(class_ids),
                        CohortStaffAssignment.project_id.in_(project_ids),
                    ),
                )
                .order_by(
                    CohortStaffAssignment.valid_from,
                    CohortStaffAssignment.id,
                )
            ).tuples()
        )

    def _staff_permission_assignment_ids(self, *, person_id: UUID) -> set[UUID]:
        return set(
            self.db.scalars(
                select(CohortStaffAssignmentPermission.assignment_id)
                .join(
                    CohortStaffAssignment,
                    CohortStaffAssignment.id
                    == CohortStaffAssignmentPermission.assignment_id,
                )
                .join(
                    Permission,
                    Permission.id == CohortStaffAssignmentPermission.permission_id,
                )
                .where(
                    CohortStaffAssignment.staff_person_id == person_id,
                    Permission.key == _REPORT_WRITE_PERMISSION,
                )
            )
        )

    @staticmethod
    def _effective_end(
        *,
        valid_until: datetime | None,
        ended_at: datetime | None,
    ) -> datetime | None:
        candidates = [value for value in (valid_until, ended_at) if value is not None]
        return min(candidates) if candidates else None

    @staticmethod
    def _period_bounds(period: ReportPeriod) -> tuple[datetime, datetime]:
        starts_at = datetime.combine(
            period.starts_on,
            time.min,
            tzinfo=BUSINESS_TIME_ZONE,
        )
        ends_at = datetime.combine(
            period.ends_on + timedelta(days=1),
            time.min,
            tzinfo=BUSINESS_TIME_ZONE,
        )
        return starts_at, ends_at

    @classmethod
    def _overlaps(
        cls,
        *,
        valid_from: datetime,
        valid_until: datetime | None,
        period: ReportPeriod,
    ) -> bool:
        period_start, period_end = cls._period_bounds(period)
        return valid_from < period_end and (
            valid_until is None or valid_until > period_start
        )

    @staticmethod
    def _coach_author(
        *,
        account: UserAccount,
        assignment: CoachAssignment,
    ) -> TeachingReportAuthor:
        primary = assignment.assignment_type == "primary"
        return TeachingReportAuthor(
            person_id=account.person_id,
            user_id=account.id,
            source="coach_assignment",
            coach_assignment_id=assignment.id,
            staff_assignment_id=None,
            role_key="primary_coach" if primary else "assistant_coach",
            role_name="主教练" if primary else "助教",
            role_category="teaching",
            valid_from=assignment.valid_from,
            valid_until=TeachingReportAccessService._effective_end(
                valid_until=assignment.valid_until,
                ended_at=assignment.ended_at,
            ),
        )

    @staticmethod
    def _staff_author(
        *,
        account: UserAccount,
        assignment: CohortStaffAssignment,
        role: EvaluationStaffRole,
    ) -> TeachingReportAuthor:
        return TeachingReportAuthor(
            person_id=account.person_id,
            user_id=account.id,
            source="staff_assignment",
            coach_assignment_id=None,
            staff_assignment_id=assignment.id,
            role_key=role.key,
            role_name=assignment.role_name_snapshot,
            role_category=assignment.role_category_snapshot,
            valid_from=assignment.valid_from,
            valid_until=TeachingReportAccessService._effective_end(
                valid_until=assignment.valid_until,
                ended_at=assignment.ended_at,
            ),
        )

    def _author_for_period(
        self,
        *,
        account: UserAccount,
        period: ReportPeriod,
        coach_assignments: tuple[CoachAssignment, ...],
        staff_assignments: tuple[tuple[CohortStaffAssignment, EvaluationStaffRole], ...],
        general_allow: bool,
        attached_staff_permissions: set[UUID],
    ) -> TeachingReportAuthor | None:
        coach_candidates = sorted(
            coach_assignments,
            key=lambda assignment: (
                assignment.assignment_type != "primary",
                assignment.valid_from,
                assignment.id,
            ),
        )
        for coach_assignment in coach_candidates:
            author = self._coach_author(
                account=account,
                assignment=coach_assignment,
            )
            if not self._overlaps(
                valid_from=author.valid_from,
                valid_until=author.valid_until,
                period=period,
            ):
                continue
            if coach_assignment.assignment_type == "primary" or general_allow:
                return author
        for staff_assignment, role in staff_assignments:
            author = self._staff_author(
                account=account,
                assignment=staff_assignment,
                role=role,
            )
            if not self._overlaps(
                valid_from=author.valid_from,
                valid_until=author.valid_until,
                period=period,
            ):
                continue
            if general_allow or staff_assignment.id in attached_staff_permissions:
                return author
        return None

    def author_slots_for_enrollments(
        self,
        *,
        account_id: UUID,
        enrollment_ids: Iterable[UUID],
        occurred_at: datetime,
    ) -> dict[UUID, tuple[AuthorizedTeachingReportSlot, ...]]:
        self._require_aware(occurred_at)
        requested_ids = set(enrollment_ids)
        if not requested_ids:
            return {}
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            raise self._not_found()
        account, _ = self._active_author_account(account_id)
        targets = self._targets(requested_ids, occurred_at=occurred_at)
        center_decisions = self.access.decide_resources(
            account_id=account.id,
            permission="evaluation_center.access",
            resources=(target.resource for target in targets),
            occurred_at=occurred_at,
        )
        targets = tuple(
            target for target, decision in zip(targets, center_decisions, strict=True)
            if decision.allowed
        )
        decisions = self.access.decide_resources(
            account_id=account.id,
            permission=_REPORT_WRITE_PERMISSION,
            resources=(target.resource for target in targets),
            occurred_at=occurred_at,
        )
        coaches = self._coach_assignments_for_classes(
            person_id=account.person_id,
            class_ids={target.class_id for target in targets},
            occurred_at=occurred_at,
        )
        staff = self._staff_assignments_for_targets(
            person_id=account.person_id,
            targets=targets,
            occurred_at=occurred_at,
        )
        attached_staff_permissions = self._staff_permission_assignment_ids(
            person_id=account.person_id
        )
        existing: dict[UUID, dict[tuple[str, object], StudentReport]] = {}
        for report in self.db.scalars(
            select(StudentReport).where(
                StudentReport.enrollment_id.in_(requested_ids),
                StudentReport.author_person_id == account.person_id,
            )
        ):
            existing.setdefault(report.enrollment_id, {})[
                (report.report_kind, report.period_start_date)
            ] = report

        result: dict[UUID, tuple[AuthorizedTeachingReportSlot, ...]] = {}
        for target, decision in zip(targets, decisions, strict=True):
            if decision.reason in {"explicit_deny", "inactive_account"}:
                continue
            general_allow = decision.reason in {"role_allow", "account_allow"}
            target_coaches = tuple(
                assignment
                for assignment in coaches
                if assignment.class_id == target.class_id
            )
            target_staff = tuple(
                (assignment, role)
                for assignment, role in staff
                if assignment.cohort_id == target.cohort_id
                and (
                    assignment.class_id == target.class_id
                    or assignment.project_id == target.project_id
                )
            )
            periods = (
                *daily_periods(
                    starts_at=target.cohort_starts_at,
                    ends_at=target.cohort_ends_at,
                    occurred_at=occurred_at,
                ),
                *weekly_periods(
                    starts_at=target.cohort_starts_at,
                    ends_at=target.cohort_ends_at,
                    occurred_at=occurred_at,
                ),
            )
            target_existing = existing.get(target.enrollment_id, {})
            slots: list[AuthorizedTeachingReportSlot] = []
            for period in periods:
                author = self._author_for_period(
                    account=account,
                    period=period,
                    coach_assignments=target_coaches,
                    staff_assignments=target_staff,
                    general_allow=general_allow,
                    attached_staff_permissions=attached_staff_permissions,
                )
                if author is None:
                    continue
                opens_at = submission_opens_at(period)
                deadline = submission_deadline(
                    period,
                    cohort_ends_at=target.cohort_ends_at,
                )
                draft_opens_at, _ = self._period_bounds(period)
                existing_report = target_existing.get(
                    (period.kind, period.starts_on)
                )
                slots.append(
                    AuthorizedTeachingReportSlot(
                        target=target,
                        author=author,
                        period=period,
                        submission_opens_at=opens_at,
                        initial_submission_deadline=deadline,
                        draft_allowed=draft_opens_at <= occurred_at <= deadline,
                        submit_allowed=opens_at <= occurred_at <= deadline,
                        report_id=(
                            existing_report.id
                            if existing_report is not None
                            else None
                        ),
                        report_status=(
                            existing_report.status
                            if existing_report is not None
                            else None
                        ),
                    )
                )
            if slots:
                result[target.enrollment_id] = tuple(
                    sorted(
                        slots,
                        key=lambda slot: (
                            slot.period.starts_on,
                            slot.period.kind != "daily",
                            slot.period.sequence,
                        ),
                    )
                )
        return result

    def author_slots(
        self,
        *,
        account_id: UUID,
        enrollment_id: UUID,
        occurred_at: datetime,
    ) -> tuple[AuthorizedTeachingReportSlot, ...]:
        slots = self.author_slots_for_enrollments(
            account_id=account_id,
            enrollment_ids=(enrollment_id,),
            occurred_at=occurred_at,
        ).get(enrollment_id)
        if slots is None:
            raise self._not_found()
        return slots
