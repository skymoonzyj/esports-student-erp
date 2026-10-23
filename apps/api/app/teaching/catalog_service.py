from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.transactions import write_transaction
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.identity.models import Person
from app.identity.readiness import GuardianReadinessPolicy
from app.rbac.service import RbacService
from app.teaching.errors import (
    DuplicateProfessionalTeam,
    DuplicateProject,
    InactiveProject,
    ProfessionalTeamNotFound,
    ProjectNotFound,
    TeachingServiceError,
    TeamDivisionAccessDenied,
    TeamProjectImmutable,
)
from app.teaching.models import GameProject, ProfessionalTeam
from app.teaching.schemas import (
    ProfessionalTeamCreate,
    ProfessionalTeamUpdate,
    ProjectCreate,
    ProjectUpdate,
)
from app.team_management.lifecycle import TeamLifecycleService


class TeachingCatalogService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_catalog_actor(self, actor_user_id: UUID) -> UserAccount | None:
        # Catalog writes and their audit append reference the actor through FKs.
        # Acquire these identity locks before any directory lock, matching roster
        # writes instead of obtaining a late FK KEY SHARE lock on the account.
        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == actor_user_id)
        )
        if person_id is None:
            return None
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students([person_id])
        accounts = readiness.lock_accounts([person_id])
        return next((account for account in accounts if account.id == actor_user_id), None)

    def _require_division_authority(self, actor: UserAccount | None) -> None:
        person = self.db.get(Person, actor.person_id) if actor is not None else None
        rbac = RbacService(self.db)
        if (
            actor is None
            or actor.status != "active"
            or actor.force_password_change
            or person is None
            or person.deleted_at is not None
            or not rbac.account_has_role(actor.id, "super_admin")
            or not rbac.decide(
                user_id=actor.id, permission="projects.manage", resource_tokens={"global"}
            ).allowed
        ):
            raise TeamDivisionAccessDenied(
                "only an effective super administrator may change divisions"
            )

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> TeachingServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_game_projects_name":
            return DuplicateProject("project name already exists")
        if constraint == "uq_professional_teams_project_name":
            return DuplicateProfessionalTeam("professional team name already exists")
        return TeachingServiceError("teaching catalog transaction failed")

    def list_projects(
        self,
        *,
        name: str | None,
        include_inactive: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[GameProject], int]:
        self._validate_pagination(page, page_size)
        query = select(GameProject)
        if name is not None:
            query = query.where(GameProject.name == name.strip())
        if not include_inactive:
            query = query.where(GameProject.is_active.is_(True))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        projects = list(
            self.db.scalars(
                query.order_by(
                    GameProject.is_active.desc(),
                    GameProject.sort_order,
                    GameProject.name,
                    GameProject.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return projects, total

    def create_project(
        self,
        input: ProjectCreate,
        *,
        actor_user_id: UUID,
    ) -> GameProject:
        try:
            with write_transaction(self.db):
                actor = self._lock_catalog_actor(actor_user_id)
                if input.is_team_division:
                    self._require_division_authority(actor)
                project = GameProject(
                    name=input.name,
                    sort_order=input.sort_order,
                    is_active=True,
                    is_team_division=input.is_team_division,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(project)
                self.db.flush()
                profile = ProjectAnalysisProfile(
                    project_id=project.id,
                    version=1,
                    mode="manual_only",
                    adapter_id=None,
                    adapter_version=None,
                    scoring_rule_version=None,
                    required_fields=[],
                    confidence_thresholds={},
                    auto_publish_warning_levels=[],
                    publish_mode="review_required",
                    effective_from=datetime.now(UTC),
                    configured_by_user_id=actor_user_id,
                    change_reason="project default",
                )
                self.db.add(profile)
                self.db.flush()
            return project
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def update_project(
        self,
        project_id: UUID,
        input: ProjectUpdate,
        *,
        actor_user_id: UUID,
    ) -> GameProject:
        try:
            with write_transaction(self.db):
                # Any identity lock must precede the project lock, even though
                # actual-change authorization is decided using the refreshed project.
                actor = self._lock_catalog_actor(actor_user_id)
                project = self.db.scalar(
                    select(GameProject)
                    .where(GameProject.id == project_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if project is None:
                    raise ProjectNotFound("project not found")
                division_changed = (
                    input.is_team_division is not None
                    and input.is_team_division != project.is_team_division
                )
                if division_changed:
                    self._require_division_authority(actor)
                if input.is_active is False or (
                    division_changed and input.is_team_division is False
                ):
                    # Team creation and roster writes also acquire the project lock.
                    # Holding it stabilizes the complete team set without locking members.
                    list(
                        self.db.scalars(
                            select(ProfessionalTeam)
                            .where(ProfessionalTeam.project_id == project.id)
                            .order_by(ProfessionalTeam.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    )
                    TeamLifecycleService(self.db).assert_project_clear(project.id)
                if input.name is not None:
                    project.name = input.name
                if input.sort_order is not None:
                    project.sort_order = input.sort_order
                if input.is_active is not None:
                    project.is_active = input.is_active
                if input.is_team_division is not None:
                    project.is_team_division = input.is_team_division
                project.updated_by_user_id = actor_user_id
                self.db.flush()
            return project
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def list_teams(
        self,
        *,
        project_id: UUID | None,
        name: str | None,
        include_inactive: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[ProfessionalTeam], int]:
        self._validate_pagination(page, page_size)
        query = select(ProfessionalTeam)
        if project_id is not None:
            query = query.where(ProfessionalTeam.project_id == project_id)
        if name is not None:
            query = query.where(ProfessionalTeam.name == name.strip())
        if not include_inactive:
            query = query.where(ProfessionalTeam.is_active.is_(True))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        teams = list(
            self.db.scalars(
                query.order_by(
                    ProfessionalTeam.is_active.desc(),
                    ProfessionalTeam.name,
                    ProfessionalTeam.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return teams, total

    def create_team(
        self,
        input: ProfessionalTeamCreate,
        *,
        actor_user_id: UUID,
    ) -> ProfessionalTeam:
        try:
            with write_transaction(self.db):
                self._lock_catalog_actor(actor_user_id)
                project = self.db.scalar(
                    select(GameProject)
                    .where(GameProject.id == input.project_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if project is None:
                    raise ProjectNotFound("project not found")
                if not project.is_active:
                    raise InactiveProject("project is inactive")
                team = ProfessionalTeam(
                    project_id=project.id,
                    name=input.name,
                    notes=input.notes,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(team)
                self.db.flush()
            return team
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def update_team(
        self,
        team_id: UUID,
        input: ProfessionalTeamUpdate,
        *,
        actor_user_id: UUID,
    ) -> ProfessionalTeam:
        from app.team_management.write_support import lock_directory

        try:
            with write_transaction(self.db):
                self._lock_catalog_actor(actor_user_id)
                team = lock_directory(self.db, [team_id]).get(team_id)
                if team is None:
                    raise ProfessionalTeamNotFound("professional team not found")
                if input.project_id is not None and input.project_id != team.project_id:
                    raise TeamProjectImmutable("professional team project is immutable")
                if input.is_active is False:
                    TeamLifecycleService(self.db).assert_team_clear(team.id)
                if input.name is not None:
                    team.name = input.name
                if "notes" in input.model_fields_set:
                    team.notes = input.notes
                if input.is_active is not None:
                    team.is_active = input.is_active
                team.updated_by_user_id = actor_user_id
                self.db.flush()
            return team
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc
