from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.evaluations.errors import (
    EvaluationStaffServiceError,
    StaffAssignmentAlreadyEnded,
    StaffAssignmentConflict,
    StaffAssignmentNotFound,
    StaffAssignmentScopeInvalid,
    StaffNotEligible,
    StaffPermissionNotAllowed,
    StaffRoleInactive,
    StaffRoleNotFound,
)
from app.evaluations.schemas import (
    StaffAssignmentCreate,
    StaffAssignmentEnd,
    StaffRoleCreate,
    StaffServiceItem,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.identity.models import Person, StaffProfile
from app.rbac.models import Permission
from app.teaching.errors import CohortArchived, CohortNotFound
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

_ASSIGNABLE_PERMISSION_KEYS = frozenset(
    {
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.manual_input",
        "evaluation_jobs.review",
        "student_reports.write",
        "student_reports.review",
    }
)


class CohortStaffService:
    """Manages reusable staff roles and cohort-scoped staff service records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("assignment datetimes must include an offset")

    @staticmethod
    def _is_current(
        *,
        valid_from: datetime,
        valid_until: datetime | None,
        ended_at: datetime | None,
        occurred_at: datetime,
    ) -> bool:
        if occurred_at < valid_from:
            return False
        if valid_until is not None and occurred_at >= valid_until:
            return False
        return ended_at is None or occurred_at < ended_at

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> EvaluationStaffServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {
            "excl_cohort_staff_assignments_project_overlap",
            "excl_cohort_staff_assignments_class_overlap",
        }:
            return StaffAssignmentConflict("staff assignment overlaps")
        return EvaluationStaffServiceError("staff assignment transaction failed")

    def _append_audit(
        self,
        *,
        actor_user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        request_id: str,
        metadata: dict[str, str],
    ) -> None:
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata=metadata,
        )

    def _locked_cohort(self, cohort_id: UUID) -> Cohort:
        cohort = self.db.scalar(
            select(Cohort)
            .where(Cohort.id == cohort_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cohort is None:
            raise CohortNotFound("cohort not found")
        return cohort

    def _require_mutable_cohort(self, cohort_id: UUID) -> Cohort:
        cohort = self._locked_cohort(cohort_id)
        if cohort.status == "archived":
            raise CohortArchived("archived cohort is read-only")
        return cohort

    def _role(self, role_id: UUID, *, lock: bool = False) -> EvaluationStaffRole:
        query = select(EvaluationStaffRole).where(EvaluationStaffRole.id == role_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        role = self.db.scalar(query)
        if role is None:
            raise StaffRoleNotFound("staff role not found")
        return role

    def _require_eligible_staff(self, person_id: UUID) -> None:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise StaffNotEligible("person_missing_or_deleted")
        if self.db.get(StaffProfile, person_id) is None:
            raise StaffNotEligible("staff_profile_missing")
        account = self.db.scalar(
            select(UserAccount).where(
                UserAccount.person_id == person_id,
                UserAccount.status == "active",
            )
        )
        if account is None:
            raise StaffNotEligible("account_missing_or_disabled")

    def _scope_target(
        self,
        *,
        cohort_id: UUID,
        scope_kind: str,
        scope_id: UUID,
    ) -> tuple[UUID | None, UUID | None]:
        if scope_kind == "class":
            teaching_class = self.db.scalar(
                select(TeachingClass).where(
                    TeachingClass.id == scope_id,
                    TeachingClass.cohort_id == cohort_id,
                )
            )
            if teaching_class is None:
                raise StaffAssignmentScopeInvalid("class does not belong to cohort")
            return None, teaching_class.id
        exists = self.db.scalar(
            select(TeachingClass.id)
            .where(
                TeachingClass.cohort_id == cohort_id,
                TeachingClass.project_id == scope_id,
            )
            .limit(1)
        )
        if exists is None:
            raise StaffAssignmentScopeInvalid("project has no class in cohort")
        return scope_id, None

    def _permission_rows(self, permission_keys: list[str]) -> list[Permission]:
        if len(permission_keys) != len(set(permission_keys)):
            raise StaffPermissionNotAllowed("permission keys must be unique")
        if not set(permission_keys) <= _ASSIGNABLE_PERMISSION_KEYS:
            raise StaffPermissionNotAllowed("permission is not assignable to staff service")
        rows = list(
            self.db.scalars(select(Permission).where(Permission.key.in_(permission_keys)))
        )
        if len(rows) != len(permission_keys):
            raise StaffPermissionNotAllowed("permission not found")
        return rows

    def _has_coach_teaching_overlap(
        self,
        *,
        staff_person_id: UUID,
        class_id: UUID,
        valid_from: datetime,
        valid_until: datetime | None,
    ) -> bool:
        return (
            self.db.scalar(
                select(CoachAssignment.id)
                .where(
                    CoachAssignment.coach_person_id == staff_person_id,
                    CoachAssignment.class_id == class_id,
                    CoachAssignment.valid_from < (valid_until or datetime.max.replace(tzinfo=valid_from.tzinfo)),
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > valid_from,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > valid_from,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    def create_role(
        self,
        input: StaffRoleCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> EvaluationStaffRole:
        with write_transaction(self.db):
            role = EvaluationStaffRole(
                key=input.key,
                name=input.name,
                category=input.category,
                created_by_user_id=actor_user_id,
            )
            self.db.add(role)
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="evaluation_staff_role.create",
                resource_type="evaluation_staff_role",
                resource_id=role.id,
                request_id=request_id,
                metadata={"staff_role_id": str(role.id)},
            )
        return role

    def set_role_active(
        self,
        role_id: UUID,
        *,
        is_active: bool,
        actor_user_id: UUID,
        request_id: str,
    ) -> EvaluationStaffRole:
        try:
            with write_transaction(self.db):
                role = self._role(role_id, lock=True)
                role.is_active = is_active
                self.db.flush()
                self._append_audit(
                    actor_user_id=actor_user_id,
                    action="evaluation_staff_role.set_active",
                    resource_type="evaluation_staff_role",
                    resource_id=role.id,
                    request_id=request_id,
                    metadata={"staff_role_id": str(role.id)},
                )
            return role
        except IntegrityError as exc:
            raise EvaluationStaffServiceError("staff role cannot be changed") from exc

    def create_assignment(
        self,
        cohort_id: UUID,
        input: StaffAssignmentCreate,
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> CohortStaffAssignment:
        self._require_aware(input.valid_from)
        if input.valid_until is not None:
            self._require_aware(input.valid_until)
            if input.valid_until <= input.valid_from:
                raise StaffAssignmentConflict("assignment end must follow its start")
        try:
            with write_transaction(self.db):
                self._require_mutable_cohort(cohort_id)
                role = self._role(input.role_id, lock=True)
                if not role.is_active:
                    raise StaffRoleInactive("staff role is inactive")
                self._require_eligible_staff(input.staff_person_id)
                project_id, class_id = self._scope_target(
                    cohort_id=cohort_id,
                    scope_kind=input.scope_kind,
                    scope_id=input.scope_id,
                )
                if (
                    role.category == "teaching"
                    and class_id is not None
                    and self._has_coach_teaching_overlap(
                        staff_person_id=input.staff_person_id,
                        class_id=class_id,
                        valid_from=input.valid_from,
                        valid_until=input.valid_until,
                    )
                ):
                    raise StaffAssignmentConflict("teaching service already has a coach assignment")
                assignment = CohortStaffAssignment(
                    cohort_id=cohort_id,
                    role_id=role.id,
                    staff_person_id=input.staff_person_id,
                    project_id=project_id,
                    class_id=class_id,
                    role_name_snapshot=role.name,
                    role_category_snapshot=role.category,
                    valid_from=input.valid_from,
                    valid_until=input.valid_until,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(assignment)
                self.db.flush()
                for permission in self._permission_rows(input.permission_keys):
                    self.db.add(
                        CohortStaffAssignmentPermission(
                            assignment_id=assignment.id,
                            permission_id=permission.id,
                            created_by_user_id=actor_user_id,
                        )
                    )
                self.db.flush()
                self._append_audit(
                    actor_user_id=actor_user_id,
                    action="cohort_staff_assignment.create",
                    resource_type="cohort_staff_assignment",
                    resource_id=assignment.id,
                    request_id=request_id,
                    metadata={
                        "staff_assignment_id": str(assignment.id),
                        "cohort_id": str(cohort_id),
                        "scope_kind": (
                            "assigned_class"
                            if input.scope_kind == "class"
                            else input.scope_kind
                        ),
                        "scope_id": str(input.scope_id),
                    },
                )
            return assignment
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def replace_assignment_permissions(
        self,
        cohort_id: UUID,
        assignment_id: UUID,
        permission_keys: list[str],
        *,
        actor_user_id: UUID,
        request_id: str,
    ) -> CohortStaffAssignment:
        with write_transaction(self.db):
            self._require_mutable_cohort(cohort_id)
            assignment = self._assignment(cohort_id, assignment_id)
            rows = self._permission_rows(permission_keys)
            self.db.query(CohortStaffAssignmentPermission).filter_by(
                assignment_id=assignment.id
            ).delete(synchronize_session=False)
            for permission in rows:
                self.db.add(
                    CohortStaffAssignmentPermission(
                        assignment_id=assignment.id,
                        permission_id=permission.id,
                        created_by_user_id=actor_user_id,
                    )
                )
            assignment.updated_by_user_id = actor_user_id
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="cohort_staff_assignment.replace_permissions",
                resource_type="cohort_staff_assignment",
                resource_id=assignment.id,
                request_id=request_id,
                metadata={"staff_assignment_id": str(assignment.id)},
            )
        return assignment

    def _assignment(self, cohort_id: UUID, assignment_id: UUID) -> CohortStaffAssignment:
        assignment = self.db.scalar(
            select(CohortStaffAssignment)
            .where(
                CohortStaffAssignment.id == assignment_id,
                CohortStaffAssignment.cohort_id == cohort_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if assignment is None:
            raise StaffAssignmentNotFound("staff assignment not found")
        return assignment

    def end_assignment(
        self,
        cohort_id: UUID,
        assignment_id: UUID,
        input: StaffAssignmentEnd,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> CohortStaffAssignment:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            self._require_mutable_cohort(cohort_id)
            assignment = self._assignment(cohort_id, assignment_id)
            if assignment.ended_at is not None:
                raise StaffAssignmentAlreadyEnded("staff assignment is already ended")
            if occurred_at <= assignment.valid_from:
                raise StaffAssignmentConflict("assignment end must follow its start")
            assignment.ended_at = occurred_at
            assignment.ended_by_user_id = actor_user_id
            assignment.end_reason = input.reason
            assignment.updated_by_user_id = actor_user_id
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="cohort_staff_assignment.end",
                resource_type="cohort_staff_assignment",
                resource_id=assignment.id,
                request_id=request_id,
                metadata={"staff_assignment_id": str(assignment.id)},
            )
        return assignment

    def list_service_history(
        self,
        cohort_id: UUID,
        *,
        occurred_at: datetime,
    ) -> list[StaffServiceItem]:
        self._require_aware(occurred_at)
        if self.db.get(Cohort, cohort_id) is None:
            raise CohortNotFound("cohort not found")
        items: list[StaffServiceItem] = []
        for assignment, person, teaching_class, project in self.db.execute(
            select(CohortStaffAssignment, Person, TeachingClass, GameProject)
            .join(Person, Person.id == CohortStaffAssignment.staff_person_id)
            .outerjoin(TeachingClass, TeachingClass.id == CohortStaffAssignment.class_id)
            .join(
                GameProject,
                GameProject.id
                == func.coalesce(
                    CohortStaffAssignment.project_id,
                    TeachingClass.project_id,
                ),
            )
            .where(CohortStaffAssignment.cohort_id == cohort_id)
        ):
            target_project_id = assignment.project_id or teaching_class.project_id
            items.append(
                StaffServiceItem(
                    id=assignment.id,
                    source="cohort_staff_assignment",
                    staff_person_id=person.id,
                    staff_name=person.full_name,
                    role_name=assignment.role_name_snapshot,
                    role_category=assignment.role_category_snapshot,
                    scope_kind="project" if assignment.project_id is not None else "class",
                    project_id=target_project_id,
                    project_name=project.name,
                    class_id=teaching_class.id if teaching_class is not None else None,
                    class_name=teaching_class.name if teaching_class is not None else None,
                    valid_from=assignment.valid_from,
                    valid_until=assignment.valid_until,
                    ended_at=assignment.ended_at,
                    end_reason=assignment.end_reason,
                    manage_allowed=self._is_current(
                        valid_from=assignment.valid_from,
                        valid_until=assignment.valid_until,
                        ended_at=assignment.ended_at,
                        occurred_at=occurred_at,
                    ),
                )
            )
        for assignment, person, teaching_class, project in self.db.execute(
            select(CoachAssignment, Person, TeachingClass, GameProject)
            .join(Person, Person.id == CoachAssignment.coach_person_id)
            .join(TeachingClass, TeachingClass.id == CoachAssignment.class_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(TeachingClass.cohort_id == cohort_id)
        ):
            items.append(
                StaffServiceItem(
                    id=assignment.id,
                    source="coach_assignment",
                    staff_person_id=person.id,
                    staff_name=person.full_name,
                    role_name="主教练" if assignment.assignment_type == "primary" else "助教",
                    role_category="teaching",
                    scope_kind="class",
                    project_id=project.id,
                    project_name=project.name,
                    class_id=teaching_class.id,
                    class_name=teaching_class.name,
                    valid_from=assignment.valid_from,
                    valid_until=assignment.valid_until,
                    ended_at=assignment.ended_at,
                    end_reason=assignment.end_reason,
                    manage_allowed=False,
                )
            )
        return sorted(items, key=lambda item: (item.valid_from, str(item.id)), reverse=True)
