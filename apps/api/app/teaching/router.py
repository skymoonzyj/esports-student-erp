from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.identity.models import Person
from app.rbac.service import RbacService
from app.teaching.access import TeachingVisibility
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.coach_service import CoachService
from app.teaching.cohort_service import CohortService
from app.teaching.errors import (
    ClassNotFound,
    CoachAssignmentAlreadyEnded,
    CoachAssignmentConflict,
    CoachAssignmentNotFound,
    CoachNotEligible,
    CohortArchived,
    CohortNotFound,
    CohortScheduleLocked,
    DuplicateProfessionalTeam,
    DuplicateProject,
    InactiveProject,
    InvalidCohortTransitionError,
    PrimaryCoachConflict,
    PrimaryCoachRequired,
    ProfessionalTeamNotFound,
    ProjectNotFound,
    TeachingServiceError,
    TeamDivisionAccessDenied,
    TeamProjectImmutable,
)
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.project_coach_service import ProjectCoachService
from app.teaching.schemas import (
    CoachAssignmentCreate,
    CoachAssignmentEndRequest,
    CoachAssignmentResponse,
    CohortCreate,
    CohortResponse,
    CohortStatusEventResponse,
    CohortTransitionRequest,
    CohortUpdate,
    EligibleCoachResponse,
    Page,
    ProfessionalTeamCreate,
    ProfessionalTeamResponse,
    ProfessionalTeamUpdate,
    ProjectCoachAssignmentUpdate,
    ProjectCoachConfigurationResponse,
    ProjectCoachOptionResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    TeachingClassCreate,
    TeachingClassResponse,
    TeachingClassUpdate,
)
from app.teaching.state import CohortStatus, automation_issues
from app.team_management.errors import TeamHasActiveRelationships


class PrivateTeachingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            response = await original(request)
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return route_handler


router = APIRouter(tags=["teaching"], route_class=PrivateTeachingRoute)


def get_catalog_service(
    db: Annotated[Session, Depends(get_db)],
) -> TeachingCatalogService:
    return TeachingCatalogService(db)


def get_cohort_service(
    db: Annotated[Session, Depends(get_db)],
) -> CohortService:
    return CohortService(db)


def get_coach_service(
    db: Annotated[Session, Depends(get_db)],
) -> CoachService:
    return CoachService(db)


def get_project_coach_service(
    db: Annotated[Session, Depends(get_db)],
) -> ProjectCoachService:
    return ProjectCoachService(db)


def _require_permission(
    context: AuthContext,
    db: Session,
    permission: str,
) -> None:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens={"global"},
    )
    if not decision.allowed:
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )


def _teaching_visibility(
    context: AuthContext,
    db: Session,
    *,
    permission: str,
    at: datetime,
    require_collection_access: bool,
) -> TeachingVisibility:
    visibility = RbacService(db).teaching_visibility(
        user_id=context.account.id,
        permission=permission,
        actor_person_id=context.person.id,
        at=at,
    )
    if (
        require_collection_access
        and not visibility.global_access
        and not visibility.class_ids
    ):
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )
    return visibility


def _object_not_found(*, resource: str) -> NoReturn:
    code = "class_not_found" if resource == "class" else "cohort_not_found"
    raise ProblemError(status=404, code=code, detail="教学资源不存在")


def _require_class_permission(
    context: AuthContext,
    db: Session,
    *,
    class_id: UUID,
    permission: str,
    at: datetime,
) -> TeachingClass:
    teaching_class = db.get(TeachingClass, class_id)
    if teaching_class is None:
        _object_not_found(resource="class")
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens=RbacService(db).class_resource_tokens(
            actor_person_id=context.person.id,
            class_id=class_id,
            at=at,
        ),
    )
    if not decision.allowed:
        _object_not_found(resource="class")
    return teaching_class


def _require_cohort_permission(
    context: AuthContext,
    db: Session,
    *,
    cohort_id: UUID,
    permission: str,
    at: datetime,
) -> Cohort:
    cohort = db.get(Cohort, cohort_id)
    if cohort is None:
        _object_not_found(resource="cohort")
    if not RbacService(db).cohort_permission_allowed(
        user_id=context.account.id,
        permission=permission,
        actor_person_id=context.person.id,
        cohort_id=cohort_id,
        at=at,
    ):
        _object_not_found(resource="cohort")
    return cohort


def _raise_catalog_error(exc: TeachingServiceError | TeamHasActiveRelationships) -> NoReturn:
    if isinstance(exc, TeamDivisionAccessDenied):
        raise ProblemError(
            status=403, code=exc.code, detail="仅有效超级管理员可变更队伍分部"
        ) from exc
    if isinstance(exc, TeamHasActiveRelationships):
        raise ProblemError(status=409, code=exc.code, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            ProjectNotFound,
            ProfessionalTeamNotFound,
            CohortNotFound,
            ClassNotFound,
            CoachAssignmentNotFound,
        ),
    ):
        raise ProblemError(status=404, code=exc.code, detail="教学资源不存在") from exc
    if isinstance(
        exc,
        (
            DuplicateProject,
            DuplicateProfessionalTeam,
            InactiveProject,
            TeamProjectImmutable,
            InvalidCohortTransitionError,
            PrimaryCoachRequired,
            CohortScheduleLocked,
            CohortArchived,
            PrimaryCoachConflict,
            CoachAssignmentConflict,
            CoachAssignmentAlreadyEnded,
        ),
    ):
        raise ProblemError(status=409, code=exc.code, detail="教学资源冲突") from exc
    detail = "所选人员当前不是可用教练" if isinstance(exc, CoachNotEligible) else "教学资源请求无效"
    raise ProblemError(status=422, code=exc.code, detail=detail) from exc


def _cohort_response(
    service: CohortService,
    cohort: Cohort,
    *,
    now: datetime,
    visibility: TeachingVisibility | None = None,
) -> CohortResponse:
    status = cast(CohortStatus, cohort.status)
    return CohortResponse(
        id=cohort.id,
        name=cohort.name,
        starts_at=cohort.starts_at,
        ends_at=cohort.ends_at,
        default_check_in_at=cohort.default_check_in_at,
        default_check_out_at=cohort.default_check_out_at,
        status=status,
        auto_start_enabled=cohort.auto_start_enabled,
        auto_complete_enabled=cohort.auto_complete_enabled,
        automation_issues=automation_issues(
            status=status,
            starts_at=cohort.starts_at,
            ends_at=cohort.ends_at,
            auto_start_enabled=cohort.auto_start_enabled,
            auto_complete_enabled=cohort.auto_complete_enabled,
            now=now,
        ),
        enabled_class_count=service.enabled_class_count(
            cohort.id,
            visibility=visibility,
        ),
        missing_primary_class_count=service.missing_primary_class_count(
            cohort.id,
            at=now,
            visibility=visibility,
        ),
        created_at=cohort.created_at,
        updated_at=cohort.updated_at,
    )


def _coach_assignment_names(
    db: Session,
    assignments: list[CoachAssignment],
) -> dict[UUID, str]:
    coach_ids = {assignment.coach_person_id for assignment in assignments}
    if not coach_ids:
        return {}
    return {
        person_id: full_name
        for person_id, full_name in db.execute(
            select(Person.id, Person.full_name).where(Person.id.in_(coach_ids))
        ).tuples()
    }


def _coach_assignment_response(
    assignment: CoachAssignment,
    *,
    coach_full_name: str,
) -> CoachAssignmentResponse:
    return CoachAssignmentResponse(
        id=assignment.id,
        class_id=assignment.class_id,
        coach_person_id=assignment.coach_person_id,
        coach_full_name=coach_full_name,
        assignment_type=cast(
            Literal["primary", "assistant"],
            assignment.assignment_type,
        ),
        valid_from=assignment.valid_from,
        valid_until=assignment.valid_until,
        ended_at=assignment.ended_at,
        ended_by_user_id=assignment.ended_by_user_id,
        end_reason=assignment.end_reason,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _append_catalog_audit(
    *,
    db: Session,
    request: Request,
    context: AuthContext,
    action: str,
    resource_type: str,
    resource_id: UUID,
    metadata: dict[str, Any],
) -> None:
    AuditService(db).append(
        actor_user_id=context.account.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata=metadata,
    )


def _project_coach_configuration_response(
    project_id: UUID,
    project_name: str,
    coaches: list[tuple[Person, bool]],
) -> ProjectCoachConfigurationResponse:
    return ProjectCoachConfigurationResponse(
        project_id=project_id,
        project_name=project_name,
        coaches=[
            ProjectCoachOptionResponse(
                person_id=coach.id,
                full_name=coach.full_name,
                assigned=assigned,
            )
            for coach, assigned in coaches
        ],
    )


@router.get("/projects", response_model=Page[ProjectResponse])
def list_projects(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str | None, Query(max_length=80)] = None,
    include_inactive: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ProjectResponse]:
    _require_permission(context, db, "teaching.read")
    projects, total = service.list_projects(
        name=name,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return Page[ProjectResponse](
        items=[ProjectResponse.model_validate(project) for project in projects],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    input: ProjectCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectResponse:
    _require_permission(context, db, "projects.manage")
    try:
        db.commit()
        with db.begin():
            project = service.create_project(input, actor_user_id=context.account.id)
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.project.create",
                resource_type="game_project",
                resource_id=project.id,
                metadata={"project_id": str(project.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return ProjectResponse.model_validate(project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    input: ProjectUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectResponse:
    _require_permission(context, db, "projects.manage")
    try:
        db.commit()
        with db.begin():
            project = service.update_project(
                project_id,
                input,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.project.update",
                resource_type="game_project",
                resource_id=project.id,
                metadata={"project_id": str(project.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except (TeachingServiceError, TeamHasActiveRelationships) as exc:
        _raise_catalog_error(exc)
    return ProjectResponse.model_validate(project)


@router.get(
    "/projects/{project_id}/coaches",
    response_model=ProjectCoachConfigurationResponse,
)
def get_project_coaches(
    project_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[ProjectCoachService, Depends(get_project_coach_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectCoachConfigurationResponse:
    _require_permission(context, db, "project_coaches.manage")
    try:
        project, coaches = service.configuration(project_id)
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return _project_coach_configuration_response(project.id, project.name, coaches)


@router.put(
    "/projects/{project_id}/coaches",
    response_model=ProjectCoachConfigurationResponse,
)
def replace_project_coaches(
    project_id: UUID,
    input: ProjectCoachAssignmentUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[ProjectCoachService, Depends(get_project_coach_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectCoachConfigurationResponse:
    _require_permission(context, db, "project_coaches.manage")
    try:
        db.commit()
        with db.begin():
            project, coaches = service.replace_assignments(
                project_id,
                input.coach_person_ids,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.project_coaches.replace",
                resource_type="game_project",
                resource_id=project.id,
                metadata={
                    "project_id": str(project.id),
                    "result_count": len(input.coach_person_ids),
                },
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return _project_coach_configuration_response(project.id, project.name, coaches)


@router.get("/professional-teams", response_model=Page[ProfessionalTeamResponse])
def list_professional_teams(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
    project_id: UUID | None = None,
    name: Annotated[str | None, Query(max_length=120)] = None,
    include_inactive: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ProfessionalTeamResponse]:
    _require_permission(context, db, "teaching.read")
    teams, total = service.list_teams(
        project_id=project_id,
        name=name,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return Page[ProfessionalTeamResponse](
        items=[ProfessionalTeamResponse.model_validate(team) for team in teams],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/professional-teams",
    response_model=ProfessionalTeamResponse,
    status_code=201,
)
def create_professional_team(
    input: ProfessionalTeamCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProfessionalTeamResponse:
    _require_permission(context, db, "professional_teams.manage")
    try:
        db.commit()
        with db.begin():
            team = service.create_team(input, actor_user_id=context.account.id)
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.professional_team.create",
                resource_type="professional_team",
                resource_id=team.id,
                metadata={
                    "project_id": str(team.project_id),
                    "professional_team_id": str(team.id),
                },
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return ProfessionalTeamResponse.model_validate(team)


@router.patch(
    "/professional-teams/{team_id}",
    response_model=ProfessionalTeamResponse,
)
def update_professional_team(
    team_id: UUID,
    input: ProfessionalTeamUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[TeachingCatalogService, Depends(get_catalog_service)],
    db: Annotated[Session, Depends(get_db)],
) -> ProfessionalTeamResponse:
    _require_permission(context, db, "professional_teams.manage")
    try:
        db.commit()
        with db.begin():
            team = service.update_team(
                team_id,
                input,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.professional_team.update",
                resource_type="professional_team",
                resource_id=team.id,
                metadata={
                    "project_id": str(team.project_id),
                    "professional_team_id": str(team.id),
                },
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except (TeachingServiceError, TeamHasActiveRelationships) as exc:
        _raise_catalog_error(exc)
    return ProfessionalTeamResponse.model_validate(team)


@router.get("/cohorts", response_model=Page[CohortResponse])
def list_cohorts(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str | None, Query(max_length=120)] = None,
    status: CohortStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[CohortResponse]:
    now = datetime.now(UTC)
    visibility = _teaching_visibility(
        context,
        db,
        permission="teaching.read",
        at=now,
        require_collection_access=True,
    )
    try:
        service.reconcile_due_batch_for_request(
            now=now,
            request_id=str(request.state.request_id),
            visibility=visibility,
        )
        cohorts, total = service.list_cohorts(
            name=name,
            status=status,
            date_from=date_from,
            date_to=date_to,
            visibility=visibility,
            page=page,
            page_size=page_size,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    except ValueError as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="营期筛选无效",
        ) from exc
    return Page[CohortResponse](
        items=[
            _cohort_response(service, cohort, now=now, visibility=visibility)
            for cohort in cohorts
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/cohorts", response_model=CohortResponse, status_code=201)
def create_cohort(
    input: CohortCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CohortResponse:
    _require_permission(context, db, "cohorts.manage")
    now = datetime.now(UTC)
    try:
        db.commit()
        with db.begin():
            cohort = service.create_cohort(input, actor_user_id=context.account.id)
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.cohort.create",
                resource_type="cohort",
                resource_id=cohort.id,
                metadata={"cohort_id": str(cohort.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return _cohort_response(service, cohort, now=now)


@router.patch("/cohorts/{cohort_id}", response_model=CohortResponse)
def update_cohort(
    cohort_id: UUID,
    input: CohortUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CohortResponse:
    now = datetime.now(UTC)
    _require_cohort_permission(
        context,
        db,
        cohort_id=cohort_id,
        permission="cohorts.manage",
        at=now,
    )
    try:
        db.commit()
        with db.begin():
            cohort = service.update_cohort(
                cohort_id,
                input,
                actor_user_id=context.account.id,
                occurred_at=now,
                request_id=str(request.state.request_id),
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.cohort.update",
                resource_type="cohort",
                resource_id=cohort.id,
                metadata={"cohort_id": str(cohort.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    except ValueError as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="营期日程无效",
        ) from exc
    return _cohort_response(service, cohort, now=now)


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
def get_cohort(
    cohort_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CohortResponse:
    now = datetime.now(UTC)
    visibility = _teaching_visibility(
        context,
        db,
        permission="teaching.read",
        at=now,
        require_collection_access=False,
    )
    try:
        service.get_cohort(cohort_id, visibility=visibility)
        service.reconcile_due_cohort(
            cohort_id,
            now=now,
            trigger="request_reconciliation",
            request_id=str(request.state.request_id),
        )
        cohort = service.get_cohort(
            cohort_id,
            visibility=visibility,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return _cohort_response(service, cohort, now=now, visibility=visibility)


@router.get(
    "/cohorts/{cohort_id}/status-events",
    response_model=Page[CohortStatusEventResponse],
)
def list_cohort_status_events(
    cohort_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[CohortStatusEventResponse]:
    now = datetime.now(UTC)
    visibility = _teaching_visibility(
        context,
        db,
        permission="teaching.read",
        at=now,
        require_collection_access=False,
    )
    try:
        service.get_cohort(cohort_id, visibility=visibility)
        service.reconcile_due_cohort(
            cohort_id,
            now=now,
            trigger="request_reconciliation",
            request_id=str(request.state.request_id),
        )
        events, total = service.list_status_events(
            cohort_id,
            visibility=visibility,
            page=page,
            page_size=page_size,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return Page[CohortStatusEventResponse](
        items=[CohortStatusEventResponse.model_validate(event) for event in events],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/cohorts/{cohort_id}/transitions", response_model=CohortResponse)
def transition_cohort(
    cohort_id: UUID,
    input: CohortTransitionRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CohortResponse:
    now = datetime.now(UTC)
    _require_cohort_permission(
        context,
        db,
        cohort_id=cohort_id,
        permission="cohorts.manage",
        at=now,
    )
    try:
        service.reconcile_due_cohort(
            cohort_id,
            now=now,
            trigger="request_reconciliation",
            request_id=str(request.state.request_id),
        )
        cohort = service.transition(
            cohort_id,
            target=input.target,
            trigger="manual",
            actor_user_id=context.account.id,
            occurred_at=now,
            request_id=str(request.state.request_id),
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return _cohort_response(service, cohort, now=now)


@router.get("/classes", response_model=Page[TeachingClassResponse])
def list_classes(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
    cohort_id: UUID | None = None,
    project_id: UUID | None = None,
    name: Annotated[str | None, Query(max_length=120)] = None,
    is_active: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TeachingClassResponse]:
    now = datetime.now(UTC)
    visibility = _teaching_visibility(
        context,
        db,
        permission="teaching.read",
        at=now,
        require_collection_access=True,
    )
    try:
        service.reconcile_due_batch_for_request(
            now=now,
            request_id=str(request.state.request_id),
            visibility=visibility,
        )
        classes, total = service.list_classes(
            cohort_id=cohort_id,
            project_id=project_id,
            name=name,
            is_active=is_active,
            visibility=visibility,
            page=page,
            page_size=page_size,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return Page[TeachingClassResponse](
        items=[TeachingClassResponse.model_validate(item) for item in classes],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/classes", response_model=TeachingClassResponse, status_code=201)
def create_class(
    input: TeachingClassCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> TeachingClassResponse:
    now = datetime.now(UTC)
    _require_cohort_permission(
        context,
        db,
        cohort_id=input.cohort_id,
        permission="classes.manage",
        at=now,
    )
    try:
        db.commit()
        with db.begin():
            service.reconcile_due_cohort(
                input.cohort_id,
                now=now,
                trigger="request_reconciliation",
                request_id=str(request.state.request_id),
            )
            teaching_class = service.create_class(
                input,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.class.create",
                resource_type="teaching_class",
                resource_id=teaching_class.id,
                metadata={"class_id": str(teaching_class.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return TeachingClassResponse.model_validate(teaching_class)


@router.get("/classes/{class_id}", response_model=TeachingClassResponse)
def get_class(
    class_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> TeachingClassResponse:
    now = datetime.now(UTC)
    visibility = _teaching_visibility(
        context,
        db,
        permission="teaching.read",
        at=now,
        require_collection_access=False,
    )
    try:
        teaching_class = service.get_class(
            class_id,
            visibility=visibility,
        )
        service.reconcile_due_cohort(
            teaching_class.cohort_id,
            now=now,
            trigger="request_reconciliation",
            request_id=str(request.state.request_id),
        )
        teaching_class = service.get_class(
            class_id,
            visibility=visibility,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return TeachingClassResponse.model_validate(teaching_class)


@router.patch("/classes/{class_id}", response_model=TeachingClassResponse)
def update_class(
    class_id: UUID,
    input: TeachingClassUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> TeachingClassResponse:
    now = datetime.now(UTC)
    existing = _require_class_permission(
        context,
        db,
        class_id=class_id,
        permission="classes.manage",
        at=now,
    )
    try:
        cohort_id = existing.cohort_id
        db.commit()
        with db.begin():
            service.reconcile_due_cohort(
                cohort_id,
                now=now,
                trigger="request_reconciliation",
                request_id=str(request.state.request_id),
            )
            teaching_class = service.update_class(
                class_id,
                input,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.class.update",
                resource_type="teaching_class",
                resource_id=teaching_class.id,
                metadata={"class_id": str(teaching_class.id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return TeachingClassResponse.model_validate(teaching_class)


@router.get(
    "/classes/{class_id}/coach-assignments",
    response_model=Page[CoachAssignmentResponse],
)
def list_coach_assignments(
    class_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CoachService, Depends(get_coach_service)],
    db: Annotated[Session, Depends(get_db)],
    include_ended: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[CoachAssignmentResponse]:
    now = datetime.now(UTC)
    _require_class_permission(
        context,
        db,
        class_id=class_id,
        permission="teaching.read",
        at=now,
    )
    try:
        assignments, total = service.list_assignments(
            class_id,
            include_ended=include_ended,
            page=page,
            page_size=page_size,
        )
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    coach_names = _coach_assignment_names(db, assignments)
    return Page[CoachAssignmentResponse](
        items=[
            _coach_assignment_response(
                item,
                coach_full_name=coach_names[item.coach_person_id],
            )
            for item in assignments
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/classes/{class_id}/eligible-coaches",
    response_model=Page[EligibleCoachResponse],
)
def list_eligible_coaches(
    class_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CoachService, Depends(get_coach_service)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[EligibleCoachResponse]:
    now = datetime.now(UTC)
    _require_class_permission(
        context,
        db,
        class_id=class_id,
        permission="coach_assignments.manage",
        at=now,
    )
    try:
        people, total = service.eligible_coaches(
            class_id,
            query=query,
            page=page,
            page_size=page_size,
        )
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    return Page[EligibleCoachResponse](
        items=[EligibleCoachResponse.model_validate(person) for person in people],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/classes/{class_id}/coach-assignments",
    response_model=CoachAssignmentResponse,
    status_code=201,
)
def create_coach_assignment(
    class_id: UUID,
    input: CoachAssignmentCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CoachService, Depends(get_coach_service)],
    cohort_service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CoachAssignmentResponse:
    now = datetime.now(UTC)
    teaching_class = _require_class_permission(
        context,
        db,
        class_id=class_id,
        permission="coach_assignments.manage",
        at=now,
    )
    cohort_id = teaching_class.cohort_id
    try:
        db.commit()
        with db.begin():
            cohort_service.reconcile_due_cohort(
                cohort_id,
                now=now,
                trigger="request_reconciliation",
                request_id=str(request.state.request_id),
            )
            assignment = service.create_assignment(
                class_id,
                input,
                actor_user_id=context.account.id,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.coach_assignment.create",
                resource_type="coach_assignment",
                resource_id=assignment.id,
                metadata={"class_id": str(class_id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    coach_names = _coach_assignment_names(db, [assignment])
    return _coach_assignment_response(
        assignment,
        coach_full_name=coach_names[assignment.coach_person_id],
    )


@router.post(
    "/classes/{class_id}/coach-assignments/{assignment_id}/end",
    response_model=CoachAssignmentResponse,
)
def end_coach_assignment(
    class_id: UUID,
    assignment_id: UUID,
    input: CoachAssignmentEndRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[CoachService, Depends(get_coach_service)],
    cohort_service: Annotated[CohortService, Depends(get_cohort_service)],
    db: Annotated[Session, Depends(get_db)],
) -> CoachAssignmentResponse:
    now = datetime.now(UTC)
    teaching_class = _require_class_permission(
        context,
        db,
        class_id=class_id,
        permission="coach_assignments.manage",
        at=now,
    )
    cohort_id = teaching_class.cohort_id
    try:
        db.commit()
        with db.begin():
            cohort_service.reconcile_due_cohort(
                cohort_id,
                now=now,
                trigger="request_reconciliation",
                request_id=str(request.state.request_id),
            )
            assignment = service.end_assignment(
                class_id,
                assignment_id,
                input,
                actor_user_id=context.account.id,
                occurred_at=now,
            )
            _append_catalog_audit(
                db=db,
                request=request,
                context=context,
                action="teaching.coach_assignment.end",
                resource_type="coach_assignment",
                resource_id=assignment.id,
                metadata={"class_id": str(class_id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="teaching_audit_failed",
            detail="教学配置审计记录失败",
        ) from exc
    except TeachingServiceError as exc:
        _raise_catalog_error(exc)
    coach_names = _coach_assignment_names(db, [assignment])
    return _coach_assignment_response(
        assignment,
        coach_full_name=coach_names[assignment.coach_person_id],
    )
