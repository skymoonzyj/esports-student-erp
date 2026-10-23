from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, SQLColumnExpression, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.schemas import EvaluationAdminSections, GameAccountTarget
from app.identity.models import Person, StudentProfile
from app.rbac.service import RbacService
from app.teaching.models import Cohort, GameProject, TeachingClass

_GAME_ACCOUNT_PERMISSION = "student_game_accounts.manage"


@dataclass(frozen=True)
class EvaluationFoundationCapabilities:
    targets: list[GameAccountTarget]
    admin_sections: EvaluationAdminSections

    @property
    def admin_available(self) -> bool:
        return any(
            (
                self.admin_sections.cohort_staff,
                self.admin_sections.student_matching,
                self.admin_sections.analysis_profiles,
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
            authorization.allowed(
                EvaluationSqlResource(student_person_id=student_person_id)
            ),
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

    def center_allowed(self, account_id: UUID) -> bool:
        return (
            RbacService(self.db)
            .decision_context(account_id)
            .decide("evaluation_center.access", {"global"})
            .allowed
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
        student_global = bool(
            self.db.scalar(
                select(student_authorization.allowed(EvaluationSqlResource()))
            )
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
                            EvaluationSqlResource(
                                student_person_id=StudentProfile.person_id
                            )
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
        if self.db.scalar(
            select(authorization.allowed(EvaluationSqlResource()))
        ):
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

    def capabilities(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationFoundationCapabilities:
        return EvaluationFoundationCapabilities(
            targets=self.game_account_targets(
                account_id=account_id,
                occurred_at=occurred_at,
            ),
            admin_sections=self.admin_sections(
                account_id=account_id,
                occurred_at=occurred_at,
            ),
        )
