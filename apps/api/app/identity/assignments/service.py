import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import business_today
from app.core.transactions import atomic_write_transaction
from app.identity.assignments.models import (
    PersonAssignmentOperation,
    PersonAssignmentState,
    PersonProjectAssignment,
)
from app.identity.assignments.schemas import (
    AssignmentCapabilities,
    AssignmentCoach,
    AssignmentInput,
    AssignmentProject,
    AssignmentView,
    BusinessKind,
)
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.readiness import is_minor
from app.identity.student_numbers import generate_student_number
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.approved_facts import load_approved_facts
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.models import AssistedPersonProfile, CoachPersonResponsibility
from app.personnel_intake.responsibilities import replace_in_transaction
from app.personnel_intake.schemas import PersonKind
from app.rbac.models import Role, UserRole
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import GameProject, ProjectCoachAssignment
from app.team_management.models import TeamMembership
from app.team_management.write_support import lock_participants

LiteralAccountStatus = Literal["active", "disabled", "deleted", "not_activated"]


def conflict(code: str, detail: str) -> ProblemError:
    return ProblemError(status=409, code=code, detail=detail)


def invalid(detail: str) -> ProblemError:
    return ProblemError(status=422, code="assignments_invalid", detail=detail)


def identity_set(kind: BusinessKind | None) -> set[str]:
    return {"student", "player"} if kind == "both" else {kind} if kind else set()


class PersonAssignmentService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db, self.cipher, self.lookup = db, cipher, lookup

    def _authorization(
        self, actor: AuthContext, person_id: UUID, *, write: bool = False
    ) -> AssignmentCapabilities:
        roles, live_id = PersonnelIntakeAccess(self.db).actor_state(actor)
        rbac = RbacService(self.db)
        tokens = rbac.person_resource_tokens(
            actor_person_id=actor.person.id, target_person_id=person_id
        )

        def allowed(permission: str, scope: set[str] = tokens) -> bool:
            return (
                live_id is not None
                and rbac.decide(
                    user_id=actor.account.id, permission=permission, resource_tokens=scope
                ).allowed
            )

        can_edit = bool(roles & {"admin", "super_admin"}) and allowed("people.update")
        if not allowed("people.read") or write and not can_edit:
            raise ProblemError(
                status=403, code="permission_denied", detail="没有维护此人员身份与归属的权限"
            )
        return AssignmentCapabilities(
            can_edit=can_edit,
            can_manage_account_roles=allowed("permissions.manage", {"global"})
            and allowed("permissions.manage"),
        )

    def _person(self, person_id: UUID) -> Person:
        person = self.db.scalar(
            select(Person)
            .where(Person.id == person_id, Person.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        if person is None:
            raise ProblemError(status=404, code="person_not_found", detail="人员不存在")
        return person

    def _kind(
        self, person_id: UUID, state: PersonAssignmentState | None, student: StudentProfile | None
    ) -> tuple[BusinessKind | None, PersonKind | None]:
        source: PersonKind | None = None
        try:
            fact = load_approved_facts(
                self.db,
                [person_id],
                reference_date=business_today(),
                cipher=self.cipher,
                lookup=self.lookup,
            )[person_id]
            if fact.approval_valid:
                source = fact.kind
        except ApprovalEvidenceError:
            # Invalid signed intake facts never create a new business privilege.
            pass
        player = bool(state and state.is_player) or source in {"player", "both"}
        player = (
            player
            or self.db.scalar(
                select(TeamMembership.id).where(TeamMembership.person_id == person_id).limit(1)
            )
            is not None
        )
        player = (
            player
            or self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .join(UserAccount, UserAccount.id == UserRole.user_id)
                .where(UserAccount.person_id == person_id, Role.key == "team_member")
                .limit(1)
            )
            is not None
        )
        return (
            "both" if student and player else "student" if student else "player" if player else None
        ), source

    def _coaches(self, ids: set[UUID]) -> list[AssignmentCoach]:
        projects: dict[UUID, list[UUID]] = {identifier: [] for identifier in ids}
        for coach, project in self.db.execute(
            select(ProjectCoachAssignment.coach_person_id, ProjectCoachAssignment.project_id)
            .join(GameProject, GameProject.id == ProjectCoachAssignment.project_id)
            .where(
                ProjectCoachAssignment.coach_person_id.in_(ids),
                ProjectCoachAssignment.is_active,
                GameProject.is_active,
            )
            .order_by(ProjectCoachAssignment.project_id)
        ):
            projects[coach].append(project)
        return [
            AssignmentCoach(id=p.id, full_name=p.full_name, project_ids=projects[p.id])
            for p in self.db.scalars(
                select(Person).where(Person.id.in_(ids)).order_by(Person.full_name, Person.id)
            )
        ]

    def get(self, actor: AuthContext, person_id: UUID) -> AssignmentView:
        capabilities = self._authorization(actor, person_id)
        person = self._person(person_id)
        state = self.db.get(PersonAssignmentState, person_id, populate_existing=True)
        student = self.db.get(StudentProfile, person_id, populate_existing=True)
        assisted = self.db.get(AssistedPersonProfile, person_id, populate_existing=True)
        kind, source = self._kind(person_id, state, student)
        projects = list(
            self.db.scalars(
                select(PersonProjectAssignment)
                .where(
                    PersonProjectAssignment.person_id == person_id,
                    PersonProjectAssignment.ended_at.is_(None),
                )
                .order_by(PersonProjectAssignment.id)
                .execution_options(populate_existing=True)
            )
        )
        coaches = list(
            self.db.scalars(
                select(CoachPersonResponsibility)
                .where(
                    CoachPersonResponsibility.person_id == person_id,
                    CoachPersonResponsibility.ended_at.is_(None),
                )
                .order_by(CoachPersonResponsibility.id)
                .execution_options(populate_existing=True)
            )
        )
        fingerprint = [
            str(person.id),
            str(person.updated_at),
            state.version if state else 0,
            assisted.version if assisted else None,
            student.status if student else None,
            student.student_number if student else None,
            kind,
            [str(p.id) for p in projects],
            [str(c.id) for c in coaches],
        ]
        version = hashlib.sha256(
            json.dumps(fingerprint, separators=(",", ":")).encode()
        ).hexdigest()
        account = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.person_id == person_id)
            .execution_options(populate_existing=True)
        )
        eligible_projects = (
            list(
                self.db.scalars(
                    select(GameProject)
                    .where(GameProject.is_active)
                    .order_by(GameProject.sort_order, GameProject.id)
                )
            )
            if capabilities.can_edit
            else []
        )
        eligible_coach_ids = (
            set(
                self.db.scalars(
                    select(ProjectCoachAssignment.coach_person_id)
                    .join(GameProject, GameProject.id == ProjectCoachAssignment.project_id)
                    .where(
                        ProjectCoachAssignment.is_active,
                        GameProject.is_active,
                        ProjectCoachAssignment.coach_person_id != person_id,
                        CoachEligibility.eligible_person_predicate(
                            ProjectCoachAssignment.coach_person_id
                        ),
                    )
                )
            )
            if capabilities.can_edit
            else set()
        )
        project_ids = sorted(p.project_id for p in projects)
        coach_ids = sorted(c.coach_person_id for c in coaches)
        return AssignmentView(
            person_id=person_id,
            version=version,
            business_kind=kind,
            source_intake_kind=source,
            student_project_ids=project_ids,
            coach_person_ids=coach_ids,
            capabilities=capabilities,
            eligible_projects=[AssignmentProject(id=p.id, name=p.name) for p in eligible_projects],
            eligible_coaches=self._coaches(eligible_coach_ids),
            current_projects=[
                AssignmentProject(id=p.id, name=p.name)
                for p in self.db.scalars(
                    select(GameProject)
                    .where(GameProject.id.in_(project_ids))
                    .order_by(GameProject.sort_order, GameProject.id)
                )
            ],
            current_coaches=self._coaches(set(coach_ids)),
            account_id=account.id if account and capabilities.can_manage_account_roles else None,
            account_status=cast(
                "LiteralAccountStatus", account.status if account else "not_activated"
            ),
        )

    def _validate_selection(
        self, person_id: UUID, input: AssignmentInput, current: AssignmentView
    ) -> bool:
        projects = set(input.student_project_ids)
        coaches = set(input.coach_person_ids)
        edited = projects != set(current.student_project_ids) or coaches != set(
            current.coach_person_ids
        )
        if input.business_kind not in {"student", "both"}:
            if projects or coaches != set(current.coach_person_ids):
                raise invalid("项目与教练分配需要学员身份；原负责人可保持不变")
            return False
        active_projects = set(
            self.db.scalars(
                select(GameProject.id).where(GameProject.id.in_(projects), GameProject.is_active)
            )
        )
        if active_projects != projects:
            raise invalid("所选项目不存在或已停用，请重新选择")
        if edited:
            eligible = set(
                self.db.scalars(
                    select(ProjectCoachAssignment.coach_person_id).where(
                        ProjectCoachAssignment.project_id.in_(projects),
                        ProjectCoachAssignment.is_active,
                        CoachEligibility.eligible_person_predicate(
                            ProjectCoachAssignment.coach_person_id
                        ),
                    )
                )
            )
            if person_id in coaches or not coaches.issubset(eligible):
                raise invalid("教练必须仍具资格且负责至少一个所选启用项目")
        return edited

    def _create_student(self, person: Person, actor: AuthContext, now: datetime) -> None:
        status = "draft"
        if (
            self.db.get(AssistedPersonProfile, person.id) is None
            and (
                self.db.get(StaffProfile, person.id) is not None
                or self.db.get(VisitorProfile, person.id) is not None
            )
        ):
            if not (
                person.date_of_birth
                and not is_minor(person.date_of_birth, business_today())
                and person.sex
                and person.identity_ciphertext
                and person.identity_digest
                and person.email_ciphertext
                and person.email_digest
            ):
                raise conflict(
                    "student_identity_requires_completion",
                    "现有人员须先补全成年身份与联系资料，再增加学员身份",
                )
            status = "active"
        self.db.add(
            StudentProfile(
                person_id=person.id,
                student_number=generate_student_number(self.db, now),
                status=status,
            )
        )

    def replace(
        self, actor: AuthContext, person_id: UUID, input: AssignmentInput
    ) -> AssignmentView:
        digest = hashlib.sha256(
            json.dumps(
                {"person_id": str(person_id), **input.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with atomic_write_transaction(self.db):
            self._authorization(actor, person_id, write=True)
            key = int.from_bytes(
                hashlib.sha256(
                    f"person-assignments:{actor.account.id}:{input.idempotency_key}".encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            self.db.execute(select(func.pg_advisory_xact_lock(key)))
            old_coaches = set(
                self.db.scalars(
                    select(CoachPersonResponsibility.coach_person_id).where(
                        CoachPersonResponsibility.person_id == person_id,
                        CoachPersonResponsibility.ended_at.is_(None),
                    )
                )
            )
            person_ids = {actor.person.id, person_id, *input.coach_person_ids, *old_coaches}
            account_ids = set(
                self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(person_ids)))
            )
            lock_participants(self.db, sorted(person_ids), sorted(account_ids))
            self._authorization(actor, person_id, write=True)
            person = self._person(person_id)
            previous = self.db.scalar(
                select(PersonAssignmentOperation).where(
                    PersonAssignmentOperation.actor_account_id == actor.account.id,
                    PersonAssignmentOperation.idempotency_key == input.idempotency_key,
                )
            )
            if previous:
                if previous.person_id != person_id or previous.request_fingerprint != digest:
                    raise conflict(
                        "idempotency_conflict", "此操作编号已用于不同内容，请重新发起保存"
                    )
                result = AssignmentView.model_validate(previous.result)
                current = self.get(actor, person_id)
                # Historical result is replayed only after live authorization; directory
                # and account identifiers are always reprojected with today's access.
                return result.model_copy(
                    update={
                        key: getattr(current, key)
                        for key in (
                            "capabilities",
                            "eligible_projects",
                            "eligible_coaches",
                            "account_id",
                            "account_status",
                        )
                    }
                )
            list(
                self.db.scalars(
                    select(GameProject)
                    .where(GameProject.id.in_(input.student_project_ids))
                    .order_by(GameProject.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            list(
                self.db.scalars(
                    select(ProjectCoachAssignment)
                    .where(
                        ProjectCoachAssignment.project_id.in_(input.student_project_ids),
                        ProjectCoachAssignment.coach_person_id.in_(input.coach_person_ids),
                    )
                    .order_by(ProjectCoachAssignment.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            current = self.get(actor, person_id)
            if current.version != input.expected_version:
                raise conflict("assignments_stale", "身份或归属已发生变化，请刷新后重新确认")
            if not identity_set(current.business_kind).issubset(identity_set(input.business_kind)):
                raise conflict(
                    "identity_removal_not_supported",
                    "此入口仅支持增加身份，不能删除已有学员或队员身份",
                )
            try:
                AuditService.validate_purpose(input.reason)
            except AuditMetadataRejected as exc:
                raise invalid("变更原因不能包含身份证、联系方式或其他敏感内容") from exc
            replace_coaches = self._validate_selection(person_id, input, current)
            now = datetime.now(UTC)
            if (
                input.business_kind in {"student", "both"}
                and self.db.get(StudentProfile, person_id) is None
            ):
                self._create_student(person, actor, now)
            state = self.db.get(PersonAssignmentState, person_id)
            if state is None:
                state = PersonAssignmentState(
                    person_id=person_id, version=0, updated_by_user_id=actor.account.id
                )
                self.db.add(state)
            state.is_player = input.business_kind in {"player", "both"}
            state.version += 1
            state.updated_by_user_id, state.updated_at = actor.account.id, now
            rows = list(
                self.db.scalars(
                    select(PersonProjectAssignment).where(
                        PersonProjectAssignment.person_id == person_id,
                        PersonProjectAssignment.ended_at.is_(None),
                    )
                )
            )
            desired = set(input.student_project_ids)
            for row in rows:
                if row.project_id not in desired:
                    row.ended_at, row.ended_by_user_id = now, actor.account.id
            for project_id in sorted(desired - {row.project_id for row in rows}):
                self.db.add(
                    PersonProjectAssignment(
                        person_id=person_id,
                        project_id=project_id,
                        created_by_user_id=actor.account.id,
                        created_at=now,
                    )
                )
            if replace_coaches:
                replace_in_transaction(self.db, actor, person_id, input.coach_person_ids, now)
            self.db.flush()
            result = self.get(actor, person_id)
            self.db.add(
                PersonAssignmentOperation(
                    actor_account_id=actor.account.id,
                    person_id=person_id,
                    idempotency_key=input.idempotency_key,
                    request_fingerprint=digest,
                    result=result.model_dump(mode="json"),
                )
            )
            AuditService(self.db).append(
                actor_user_id=actor.account.id,
                action="person.assignments.update",
                resource_type="person",
                resource_id=person_id,
                purpose=input.reason,
                outcome="allowed",
                request_id=str(input.idempotency_key),
                metadata={"person_id": str(person_id), "new_version": state.version},
            )
            self.db.flush()
            return result
