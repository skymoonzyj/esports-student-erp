from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    SQLColumnExpression,
    case,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.orm import Session, aliased

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationResource,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.errors import CampSurveyResourceNotFound, MediaValidationFailed
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.schemas import EvaluationAdminSections, GameAccountTarget
from app.evaluations.survey_access import CampSurveyAccessService
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffRating,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
    CampSurveySupplement,
    CampSurveySupplementRating,
    CampSurveyVersion,
)
from app.evaluations.task_access import EvaluationTaskAccessService
from app.evaluations.task_schemas import (
    EvaluationAuthorizationSource,
    EvaluationProfileSlot,
    EvaluationUploadTargetResponse,
)
from app.evaluations.upload_service import EvaluationUploadService
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Role, UserRole
from app.rbac.service import RbacService
from app.teaching.models import Cohort, GameProject, TeachingClass

_GAME_ACCOUNT_PERMISSION = "student_game_accounts.manage"


@dataclass(frozen=True)
class CampSurveySurfaceCapabilities:
    respondent: bool
    staff_feedback: bool
    administration: bool

    @property
    def available(self) -> bool:
        return self.respondent or self.staff_feedback or self.administration


@dataclass(frozen=True)
class EvaluationFoundationCapabilities:
    targets: list[GameAccountTarget]
    admin_sections: EvaluationAdminSections
    upload_available: bool
    reports_available: bool
    survey_sections: CampSurveySurfaceCapabilities

    @property
    def surveys_available(self) -> bool:
        return self.survey_sections.available

    @property
    def admin_available(self) -> bool:
        return any(
            (
                self.admin_sections.cohort_staff,
                self.admin_sections.student_matching,
                self.admin_sections.analysis_profiles,
                self.admin_sections.system_tasks,
                self.admin_sections.adapter_certifications,
            )
        )


class EvaluationContextService:
    def __init__(
        self,
        db: Session,
        *,
        access: EvaluationAccessService | None = None,
    ) -> None:
        self.db = db
        self.access = access or EvaluationAccessService(db)

    @staticmethod
    def _student_allowed_via_any_project(
        authorization: EvaluationSqlContext,
        student_person_id: SQLColumnExpression[UUID],
    ) -> ColumnElement[bool]:
        return or_(
            authorization.allowed(EvaluationSqlResource(student_person_id=student_person_id)),
            select(GameProject.id)
            .where(
                authorization.allowed(
                    EvaluationSqlResource(
                        student_person_id=student_person_id,
                        project_id=GameProject.id,
                    )
                )
            )
            .exists(),
        )

    @staticmethod
    def _cohort_staff_allowed(
        authorization: EvaluationSqlContext,
        cohort_id: SQLColumnExpression[UUID],
    ) -> ColumnElement[bool]:
        return or_(
            authorization.allowed(EvaluationSqlResource(cohort_id=cohort_id)),
            select(TeachingClass.id)
            .where(
                TeachingClass.cohort_id == cohort_id,
                or_(
                    authorization.allowed(
                        EvaluationSqlResource(
                            cohort_id=cohort_id,
                            project_id=TeachingClass.project_id,
                        )
                    ),
                    authorization.allowed(
                        EvaluationSqlResource(
                            cohort_id=cohort_id,
                            project_id=TeachingClass.project_id,
                            class_id=TeachingClass.id,
                        )
                    ),
                ),
            )
            .exists(),
        )

    def center_allowed(
        self,
        account_id: UUID,
        *,
        occurred_at: datetime | None = None,
    ) -> bool:
        return (
            RbacService(self.db)
            .decision_context(account_id, occurred_at=occurred_at)
            .decide("evaluation_center.access", {"global"})
            .allowed
        )

    def _has_pending_supplement(
        self,
        *,
        cycle: CampSurveyCycle,
        submission: CampSurveySubmission,
    ) -> bool:
        """Check current-roster staff not completed in any survey history."""

        snapshot = self.db.scalar(
            select(CampSurveySnapshotVersion).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id,
                CampSurveySnapshotVersion.version == cycle.current_snapshot_version,
            )
        )
        if snapshot is None:
            return False
        current_rows = tuple(
            self.db.scalars(
                select(CampSurveyStaffSnapshot).where(
                    CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id,
                    CampSurveyStaffSnapshot.include_in_summary.is_(True),
                )
            )
        )
        if not current_rows:
            return False
        main_person_ids = set(
            self.db.scalars(
                select(CampSurveyStaffSnapshot.staff_person_id)
                .join(
                    CampSurveyStaffRating,
                    CampSurveyStaffRating.staff_snapshot_id == CampSurveyStaffSnapshot.id,
                )
                .join(
                    CampSurveyVersion,
                    CampSurveyVersion.id == CampSurveyStaffRating.version_id,
                )
                .where(CampSurveyVersion.submission_id == submission.id)
                .distinct()
            )
        )
        supplement_person_ids = set(
            self.db.scalars(
                select(CampSurveyStaffSnapshot.staff_person_id)
                .join(
                    CampSurveySupplementRating,
                    CampSurveySupplementRating.staff_snapshot_id == CampSurveyStaffSnapshot.id,
                )
                .join(
                    CampSurveySupplement,
                    CampSurveySupplement.id == CampSurveySupplementRating.supplement_id,
                )
                .where(
                    CampSurveySupplement.submission_id == submission.id,
                    CampSurveySupplement.submitted_at.is_not(None),
                )
                .distinct()
            )
        )
        completed = main_person_ids | supplement_person_ids
        return any(row.staff_person_id not in completed for row in current_rows)

    @staticmethod
    def _has_capability(context: EvaluationSqlContext) -> bool:
        return bool(
            context.account_active
            and not any(
                rule.effect == "deny" and rule.scope_kind == "global" for rule in context.overrides
            )
            and (
                context.role_rules
                or any(rule.effect == "allow" for rule in context.overrides)
                or context.staff_project_scopes
                or context.staff_class_scopes
            )
        )

    def admin_sections(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationAdminSections:
        cohort_authorization = self.access.sql_context(
            account_id=account_id,
            permission="cohort_staff.manage",
            occurred_at=occurred_at,
        )
        student_authorization = self.access.sql_context(
            account_id=account_id,
            permission=_GAME_ACCOUNT_PERMISSION,
            occurred_at=occurred_at,
        )
        analysis_authorization = self.access.sql_context(
            account_id=account_id,
            permission="analysis_profiles.manage",
            occurred_at=occurred_at,
        )
        task_authorizations = tuple(
            self.access.sql_context(
                account_id=account_id,
                permission=permission,
                occurred_at=occurred_at,
            )
            for permission in (
                "evaluation_jobs.match",
                "evaluation_jobs.manual_input",
                "evaluation_jobs.review",
            )
        )
        certification_allowed = self.access.decide(
            account_id=account_id,
            permission="evaluation_adapters.certify",
            resource=EvaluationResource(),
            occurred_at=occurred_at,
        ).allowed
        student_global = bool(
            self.db.scalar(select(student_authorization.allowed(EvaluationSqlResource())))
        )
        return EvaluationAdminSections(
            cohort_staff=(
                self.db.scalar(
                    select(Cohort.id)
                    .where(
                        self._cohort_staff_allowed(
                            cohort_authorization,
                            Cohort.id,
                        )
                    )
                    .limit(1)
                )
                is not None
            ),
            student_matching=(
                student_global
                and self.db.scalar(
                    select(StudentProfile.person_id)
                    .join(Person, Person.id == StudentProfile.person_id)
                    .where(
                        Person.deleted_at.is_(None),
                        student_authorization.allowed(
                            EvaluationSqlResource(student_person_id=StudentProfile.person_id)
                        ),
                    )
                    .limit(1)
                )
                is not None
            ),
            analysis_profiles=(
                self.db.scalar(
                    select(GameProject.id)
                    .where(
                        analysis_authorization.allowed(
                            EvaluationSqlResource(project_id=GameProject.id)
                        )
                    )
                    .limit(1)
                )
                is not None
            ),
            system_tasks=any(self._has_capability(context) for context in task_authorizations),
            adapter_certifications=certification_allowed,
        )

    def game_account_targets(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> list[GameAccountTarget]:
        account = self.db.get(UserAccount, account_id)
        if account is None or account.status != "active":
            return []
        authorization = self.access.sql_context(
            account_id=account_id,
            permission=_GAME_ACCOUNT_PERMISSION,
            occurred_at=occurred_at,
        )
        if self.db.scalar(select(authorization.allowed(EvaluationSqlResource()))):
            return []
        access_source = case(
            (Person.id == account.person_id, literal("self")),
            else_=literal("family"),
        ).label("access_source")
        active_count = (
            select(func.count(StudentGameAccount.id))
            .where(
                StudentGameAccount.student_person_id == Person.id,
                StudentGameAccount.unbound_at.is_(None),
            )
            .correlate(Person)
            .scalar_subquery()
            .label("active_count")
        )
        rows = self.db.execute(
            select(Person, StudentProfile, access_source, active_count)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .where(
                Person.deleted_at.is_(None),
                or_(
                    Person.id == account.person_id,
                    Person.id.in_(authorization.family_student_ids),
                ),
                self._student_allowed_via_any_project(
                    authorization,
                    Person.id,
                ),
            )
            .order_by(access_source, StudentProfile.student_number, Person.id)
        )
        return [
            GameAccountTarget(
                student_person_id=person.id,
                student_name=person.full_name,
                student_number=profile.student_number,
                access_source=source,
                active_count=count,
                active_limit=profile.game_account_limit,
            )
            for person, profile, source, count in rows
        ]

    def target_is_visible(
        self,
        *,
        account_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        account = self.db.get(UserAccount, account_id)
        if account is None or account.status != "active":
            return False
        authorization = self.access.sql_context(
            account_id=account_id,
            permission=_GAME_ACCOUNT_PERMISSION,
            occurred_at=occurred_at,
        )
        globally_allowed = bool(
            self.db.scalar(select(authorization.allowed(EvaluationSqlResource())))
        )
        candidate_conditions: list[ColumnElement[bool]] = []
        if not globally_allowed:
            candidate_conditions.append(
                or_(
                    Person.id == account.person_id,
                    Person.id.in_(authorization.family_student_ids),
                )
            )
        return (
            self.db.scalar(
                select(Person.id)
                .join(StudentProfile, StudentProfile.person_id == Person.id)
                .where(
                    Person.id == student_person_id,
                    Person.deleted_at.is_(None),
                    *candidate_conditions,
                    self._student_allowed_via_any_project(
                        authorization,
                        Person.id,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _masked_identity_summary(full_name: str, student_number: str) -> str:
        masked_name = "*" if len(full_name) <= 1 else full_name[0] + "*" * (len(full_name) - 1)
        visible_suffix = student_number[-3:]
        masked_number = "*" * max(1, len(student_number) - len(visible_suffix))
        return f"{masked_name} · {masked_number}{visible_suffix}"

    def upload_targets(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> list[EvaluationUploadTargetResponse]:
        """Return current authorized targets with only display-safe metadata."""

        access = EvaluationTaskAccessService(self.db, access=self.access)
        profiles = AnalysisProfileService(self.db)
        targets: list[EvaluationUploadTargetResponse] = []
        projects = self.db.scalars(
            select(GameProject)
            .where(GameProject.is_active.is_(True))
            .order_by(GameProject.sort_order, GameProject.id)
        )
        for project in projects:
            profile = profiles.current_at(project.id, occurred_at=occurred_at)
            if profile is None:
                continue
            try:
                positions, required = EvaluationUploadService.profile_slot_contract(profile)
            except MediaValidationFailed:
                continue
            definitions = {
                value.get("key"): value for value in profile.media_slots if isinstance(value, dict)
            }
            if any(
                not isinstance(definitions[key].get("label"), str) or not definitions[key]["label"]
                for key in positions
            ):
                continue
            slots = [
                EvaluationProfileSlot(
                    key=key,
                    label=cast(str, definitions[key]["label"]),
                    position=position,
                    required=key in required,
                )
                for key, position in sorted(positions.items(), key=lambda item: item[1])
            ]
            selection = access.authorized_upload_targets(
                account_id=account_id,
                project_id=project.id,
                occurred_at=occurred_at,
            )
            rows = self.db.execute(
                selection.statement.add_columns(
                    Person.full_name.label("student_full_name"),
                    StudentProfile.student_number.label("student_number"),
                    Cohort.name.label("cohort_name"),
                    TeachingClass.name.label("class_name"),
                    GameProject.name.label("project_name"),
                ).order_by(
                    Cohort.starts_at,
                    TeachingClass.name,
                    StudentProfile.student_number,
                    Enrollment.id,
                )
            ).mappings()
            for row in rows:
                target = selection.target_from_mapping(row)
                targets.append(
                    EvaluationUploadTargetResponse(
                        student_person_id=target.student_person_id,
                        enrollment_id=target.enrollment_id,
                        cohort_id=target.cohort_id,
                        project_id=target.project_id,
                        class_id=target.class_id,
                        identity_summary=self._masked_identity_summary(
                            row["student_full_name"],
                            row["student_number"],
                        ),
                        project_name=row["project_name"],
                        cohort_name=row["cohort_name"],
                        class_name=row["class_name"],
                        authorization_source=cast(
                            EvaluationAuthorizationSource,
                            target.authorization_source,
                        ),
                        requires_delegated_upload_reason=(target.requires_delegated_reason),
                        upload_window_start=target.upload_window.starts_at,
                        upload_window_end=target.upload_window.ends_at,
                        profile_id=profile.id,
                        profile_version=profile.version,
                        profile_mode=cast(
                            Literal["manual_only", "automatic"],
                            profile.mode,
                        ),
                        slots=slots,
                    )
                )
        return targets

    def survey_sections(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> CampSurveySurfaceCapabilities:
        """Return safe booleans for each current camp-survey surface.

        The query intentionally returns only a boolean.  Detailed cycle and
        respondent authorization remains in the survey access service.
        """
        account = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        if account is None:
            return CampSurveySurfaceCapabilities(False, False, False)
        # A closed cycle with no pending revision is not a usable survey
        # surface.  The same temporal and relationship predicates as the
        # respondent access service are applied here, but only as a boolean
        # capability hint; the write/read routes re-authorize independently.
        if not self.center_allowed(account_id, occurred_at=occurred_at):
            return CampSurveySurfaceCapabilities(False, False, False)
        person_id = account.person_id
        reference_date = business_date_at(occurred_at)
        has_guardian_role = (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(
                    UserRole.user_id == account.id,
                    Role.key == "guardian",
                )
                .limit(1)
            )
            is not None
        )
        family_students = select(GuardianRelationship.student_person_id).where(
            GuardianRelationship.guardian_person_id == person_id,
            GuardianRelationship.requires_verification.is_(False),
            GuardianRelationship.valid_from <= reference_date,
            or_(
                GuardianRelationship.valid_until.is_(None),
                GuardianRelationship.valid_until >= reference_date,
            ),
            or_(
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.ended_at > occurred_at,
            ),
        )
        respondent_students = CampSurveyCycle.student_person_id == person_id
        if has_guardian_role:
            respondent_students = or_(
                respondent_students,
                CampSurveyCycle.student_person_id.in_(family_students),
            )
        candidate_cycles = list(
            self.db.scalars(
                select(CampSurveyCycle)
                .join(
                    StudentProfile,
                    StudentProfile.person_id == CampSurveyCycle.student_person_id,
                )
                .join(Person, Person.id == CampSurveyCycle.student_person_id)
                .where(
                    CampSurveyCycle.opens_at <= occurred_at,
                    CampSurveyCycle.due_at > occurred_at,
                    respondent_students,
                    StudentProfile.status == "active",
                    Person.deleted_at.is_(None),
                )
            )
        )
        respondent_access = CampSurveyAccessService(self.db)
        respondent_available = False
        for cycle in candidate_cycles:
            try:
                respondent = respondent_access.resolve_respondent(
                    cycle_id=cycle.id,
                    account_id=account_id,
                    occurred_at=occurred_at,
                )
            except CampSurveyResourceNotFound:
                # The detailed route maps all respondent authorization
                # failures to private 404.  This capability hint is only a
                # boolean and must never make an invalid relationship visible.
                continue
            submission = self.db.scalar(
                select(CampSurveySubmission).where(
                    CampSurveySubmission.cycle_id == cycle.id,
                    CampSurveySubmission.respondent_person_id == respondent.person_id,
                )
            )
            if submission is None or (
                submission.revision_count == 0 and submission.latest_version == 1
            ):
                respondent_available = True
                break
            if self._has_pending_supplement(cycle=cycle, submission=submission):
                respondent_available = True
                break

        # Staff feedback is available only when a published rating still
        # belongs to the current included roster.  Historical rows are kept,
        # but a correction exclusion must also hide this entry point.
        current_snapshot = aliased(CampSurveySnapshotVersion)
        current_staff = aliased(CampSurveyStaffSnapshot)
        current_staff_exists = exists(
            select(1)
            .select_from(current_snapshot)
            .join(
                current_staff,
                current_staff.snapshot_version_id == current_snapshot.id,
            )
            .where(
                current_snapshot.cycle_id == CampSurveyCycle.id,
                current_snapshot.version == CampSurveyCycle.current_snapshot_version,
                current_staff.staff_person_id == person_id,
                current_staff.include_in_summary.is_(True),
            )
        )
        staff_feedback_available = (
            self.db.scalar(
                select(CampSurveyStaffRating.id)
                .join(
                    CampSurveyVersion,
                    CampSurveyVersion.id == CampSurveyStaffRating.version_id,
                )
                .join(
                    CampSurveySubmission,
                    CampSurveySubmission.id == CampSurveyVersion.submission_id,
                )
                .join(
                    CampSurveyStaffSnapshot,
                    CampSurveyStaffSnapshot.id == CampSurveyStaffRating.staff_snapshot_id,
                )
                .where(
                    CampSurveyStaffSnapshot.staff_person_id == person_id,
                    CampSurveyVersion.version == CampSurveySubmission.latest_version,
                    CampSurveyStaffSnapshot.include_in_summary.is_(True),
                    current_staff_exists,
                )
                .join(
                    CampSurveyCycle,
                    CampSurveyCycle.id == CampSurveySubmission.cycle_id,
                )
                .limit(1)
            )
            is not None
        )
        if not staff_feedback_available:
            staff_feedback_available = (
                self.db.scalar(
                    select(CampSurveySupplementRating.id)
                    .join(
                        CampSurveySupplement,
                        CampSurveySupplement.id == CampSurveySupplementRating.supplement_id,
                    )
                    .join(
                        CampSurveySubmission,
                        CampSurveySubmission.id == CampSurveySupplement.submission_id,
                    )
                    .join(
                        CampSurveyVersion,
                        CampSurveyVersion.submission_id == CampSurveySubmission.id,
                    )
                    .join(
                        CampSurveyStaffSnapshot,
                        CampSurveyStaffSnapshot.id
                        == CampSurveySupplementRating.staff_snapshot_id,
                    )
                    .join(
                        CampSurveyCycle,
                        CampSurveyCycle.id == CampSurveySubmission.cycle_id,
                    )
                    .where(
                        CampSurveySupplement.submitted_at.is_not(None),
                        CampSurveyVersion.version == CampSurveySubmission.latest_version,
                        CampSurveyStaffSnapshot.staff_person_id == person_id,
                        CampSurveyStaffSnapshot.include_in_summary.is_(True),
                        current_staff_exists,
                    )
                    .limit(1)
                )
                is not None
            )

        # Management capability is row-scoped.  Check global grants first,
        # then evaluate cohort/project/class resources so scoped grants work
        # without turning a global boolean into an authorization bypass.
        rbac = RbacService(self.db)
        permissions = ("camp_surveys.print", "camp_surveys.identity.read")
        administration_available = any(
            rbac.decide(
                user_id=account_id,
                permission=permission,
                resource_tokens={"global"},
                occurred_at=occurred_at,
            ).allowed
            for permission in permissions
        )
        if not administration_available:
            for permission in permissions:
                authorization = self.access.sql_context(
                    account_id=account_id,
                    permission=permission,
                    occurred_at=occurred_at,
                )
                if (
                    self.db.scalar(
                        select(CampSurveyCycle.id)
                        .join(TeachingClass, TeachingClass.id == CampSurveyCycle.class_id)
                        .outerjoin(
                            CampSurveyStaffSnapshot,
                            CampSurveyStaffSnapshot.snapshot_version_id
                            == select(CampSurveySnapshotVersion.id)
                            .where(
                                CampSurveySnapshotVersion.cycle_id == CampSurveyCycle.id,
                                CampSurveySnapshotVersion.version
                                == CampSurveyCycle.current_snapshot_version,
                            )
                            .scalar_subquery(),
                        )
                        .where(
                            or_(
                                authorization.allowed(
                                    EvaluationSqlResource(
                                        cohort_id=CampSurveyCycle.cohort_id,
                                        project_id=TeachingClass.project_id,
                                        class_id=TeachingClass.id,
                                    )
                                ),
                                authorization.allowed(
                                    EvaluationSqlResource(
                                        cohort_id=CampSurveyCycle.cohort_id,
                                        project_id=TeachingClass.project_id,
                                        staff_person_id=(
                                            CampSurveyStaffSnapshot.staff_person_id
                                        ),
                                    )
                                ),
                            )
                        )
                        .limit(1)
                    )
                    is not None
                ):
                    administration_available = True
                    break
        return CampSurveySurfaceCapabilities(
            respondent=respondent_available,
            staff_feedback=staff_feedback_available,
            administration=administration_available,
        )

    def surveys_available(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        return self.survey_sections(
            account_id=account_id,
            occurred_at=occurred_at,
        ).available

    def upload_operation_allowed(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        authorization = self.access.sql_context(
            account_id=account_id,
            permission="evaluation_media.upload",
            occurred_at=occurred_at,
        )
        if self.db.scalar(select(authorization.allowed(EvaluationSqlResource()))):
            return True
        return bool(
            self.upload_targets(
                account_id=account_id,
                occurred_at=occurred_at,
            )
        )

    def capabilities(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationFoundationCapabilities:
        upload_targets = self.upload_targets(
            account_id=account_id,
            occurred_at=occurred_at,
        )
        from app.evaluations.report_query_service import TeachingReportQueryService

        report_query_service = TeachingReportQueryService(
            self.db,
            evaluation_access=self.access,
            context=self,
        )
        report_capabilities = report_query_service.capabilities(
            account_id=account_id,
            occurred_at=occurred_at,
        )
        survey_sections = self.survey_sections(
            account_id=account_id,
            occurred_at=occurred_at,
        )
        return EvaluationFoundationCapabilities(
            targets=self.game_account_targets(
                account_id=account_id,
                occurred_at=occurred_at,
            ),
            admin_sections=self.admin_sections(
                account_id=account_id,
                occurred_at=occurred_at,
            ),
            upload_available=bool(upload_targets),
            reports_available=any(
                (
                    report_capabilities.author_available,
                    report_capabilities.reviewer_available,
                    report_capabilities.family_available,
                    report_query_service.workspace_capable(
                        account_id=account_id,
                        occurred_at=occurred_at,
                    ),
                )
            ),
            survey_sections=survey_sections,
        )
