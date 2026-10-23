from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.audit.service import AuditService
from app.auth.account_readiness import AccountAccessReadinessPolicy
from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.schemas import AccountStatus
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.housing.models import HousingBuilding, HousingFloor
from app.identity.models import Person
from app.identity.router import get_identity_service
from app.identity.service import IdentityService
from app.rbac.models import Permission, RolePermission, UserPermissionOverride
from app.rbac.policy import Effect, ScopeKind
from app.rbac.schemas import (
    AccountAccessResponse,
    EffectivePermissionResponse,
    PermissionAccountDirectoryItem,
    PermissionBuildingDirectoryItem,
    PermissionBuildingDirectoryPage,
    PermissionClassDirectoryItem,
    PermissionClassDirectoryPage,
    PermissionCohortDirectoryItem,
    PermissionCohortDirectoryPage,
    PermissionEnrollmentDirectoryItem,
    PermissionEnrollmentDirectoryPage,
    PermissionFloorDirectoryItem,
    PermissionFloorDirectoryPage,
    PermissionOverrideCreate,
    PermissionOverrideResponse,
    PermissionPersonDirectoryItem,
    PermissionPreviewResponse,
    PermissionProjectDirectoryItem,
    PermissionProjectDirectoryPage,
    PermissionResponse,
    PermissionTeamDirectoryItem,
    PermissionTeamDirectoryPage,
    RoleAssignmentRequest,
    RolePermissionResponse,
    RoleResponse,
)
from app.rbac.service import (
    DuplicatePermissionRule,
    InactivePermissionScope,
    InvalidPermissionOverride,
    LastAdminProtected,
    PermissionDelegationDenied,
    PermissionOverrideChange,
    RbacResourceNotFound,
    RbacService,
    SelfPermissionLockout,
)
from app.teaching.models import Cohort, GameProject, ProfessionalTeam, TeachingClass
from app.team_management.errors import TeamHasActiveRelationships

router = APIRouter(tags=["permissions"])


def _append_access_audit(
    *,
    db: Session,
    request: Request,
    actor_user_id: UUID,
    action: str,
    account_id: UUID,
    metadata: dict[str, Any],
) -> None:
    AuditService(db).append(
        actor_user_id=actor_user_id,
        action=action,
        resource_type="account",
        resource_id=account_id,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata=metadata,
    )


def _permission_denied() -> None:
    raise ProblemError(
        status=403,
        code="permission_denied",
        detail="没有执行此操作的权限",
    )


def _account_tokens(context: AuthContext, db: Session, account_id: UUID) -> set[str]:
    account = db.get(UserAccount, account_id)
    if account is None:
        raise ProblemError(status=404, code="rbac_resource_not_found", detail="账号不存在")
    return RbacService(db).person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=account.person_id,
    )


def _can_manage_account(
    context: AuthContext,
    db: Session,
    account_id: UUID,
) -> bool:
    return (
        RbacService(db)
        .decide(
            user_id=context.account.id,
            permission="permissions.manage",
            resource_tokens=_account_tokens(context, db, account_id),
        )
        .allowed
    )


def _can_manage_global_permissions(
    context: AuthContext,
    db: Session,
) -> bool:
    return (
        RbacService(db)
        .decide(
            user_id=context.account.id,
            permission="permissions.manage",
            resource_tokens={"global"},
        )
        .allowed
    )


def require_permissions_manager(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    if "permissions.manage" not in RbacService(db).available_permissions(
        user_id=context.account.id
    ):
        _permission_denied()
    return context


def require_global_permissions_manager(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    if not _can_manage_global_permissions(context, db):
        _permission_denied()
    return context


def require_account_permissions_manager(
    account_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    if not _can_manage_account(context, db, account_id):
        _permission_denied()
    return context


def require_global_account_permissions_manager(
    account_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    if not (
        _can_manage_global_permissions(context, db) and _can_manage_account(context, db, account_id)
    ):
        _permission_denied()
    return context


def _scope_label(db: Session, override: UserPermissionOverride) -> str | None:
    if override.scope_id is None:
        return None
    if override.scope_kind == "assigned_class":
        return RbacService(db).class_scope_label(override.scope_id)
    if override.scope_kind == "assigned_team":
        team_row = db.execute(
            select(GameProject.name, ProfessionalTeam.name)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(ProfessionalTeam.id == override.scope_id)
        ).one_or_none()
        return " · ".join(team_row) if team_row is not None else None
    if override.scope_kind == "person":
        return db.scalar(
            select(Person.full_name).where(
                Person.id == override.scope_id,
                Person.deleted_at.is_(None),
            )
        )
    if override.scope_kind == "building":
        return db.scalar(
            select(HousingBuilding.name).where(HousingBuilding.id == override.scope_id)
        )
    if override.scope_kind == "floor":
        floor_row = db.execute(
            select(HousingBuilding.name, HousingFloor.name)
            .join(HousingBuilding, HousingBuilding.id == HousingFloor.building_id)
            .where(HousingFloor.id == override.scope_id)
        ).one_or_none()
        return " · ".join(floor_row) if floor_row is not None else None
    if override.scope_kind == "cohort":
        return db.scalar(select(Cohort.name).where(Cohort.id == override.scope_id))
    if override.scope_kind == "project":
        return db.scalar(select(GameProject.name).where(GameProject.id == override.scope_id))
    if override.scope_kind == "enrollment":
        row = db.execute(
            select(Person.full_name, Cohort.name, TeachingClass.name, GameProject.name)
            .join(Enrollment, Enrollment.student_person_id == Person.id)
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(Enrollment.id == override.scope_id)
        ).one_or_none()
        return " · ".join(row) if row is not None else None
    return None


def _override_response(db: Session, override: UserPermissionOverride) -> PermissionOverrideResponse:
    return PermissionOverrideResponse(
        id=override.id,
        user_id=override.user_id,
        permission_id=override.permission_id,
        effect=cast(Effect, override.effect),
        scope_kind=cast(ScopeKind, override.scope_kind),
        scope_id=override.scope_id,
        valid_from=override.valid_from,
        valid_until=override.valid_until,
        grant_reason=override.grant_reason,
        scope_label=_scope_label(db, override),
    )


def _override_metadata(
    *,
    permission_key: str,
    effect: str,
    scope_kind: str,
    scope_id: UUID | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "permission_key": permission_key,
        "effect": effect,
        "scope_kind": scope_kind,
    }
    if scope_id is not None:
        metadata["scope_id"] = str(scope_id)
    return metadata


def _role_permissions_by_id(db: Session) -> dict[UUID, list[RolePermissionResponse]]:
    permissions_by_role: dict[UUID, list[RolePermissionResponse]] = {}
    for role_id, permission, scope_kind in db.execute(
        select(RolePermission.role_id, Permission, RolePermission.scope_kind)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .order_by(RolePermission.role_id, Permission.key, RolePermission.scope_kind)
    ).tuples():
        permissions_by_role.setdefault(role_id, []).append(
            RolePermissionResponse(
                id=permission.id,
                key=permission.key,
                name=permission.name,
                sensitive=permission.sensitive,
                scope_kind=cast(
                    Literal[
                        "global",
                        "self",
                        "family",
                        "person",
                        "assigned_class",
                        "assigned_team",
                        "building",
                        "floor",
                    ],
                    scope_kind,
                ),
            )
        )
    return permissions_by_role


def _role_response(
    role: Any,
    permissions_by_role: dict[UUID, list[RolePermissionResponse]],
) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        key=role.key,
        name=role.name,
        permissions=permissions_by_role.get(role.id, []),
    )


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    response: Response,
    _: Annotated[AuthContext, Depends(require_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> list[RoleResponse]:
    response.headers["Cache-Control"] = "no-store, private"
    service = RbacService(db)
    permissions_by_role = _role_permissions_by_id(db)
    return [_role_response(role, permissions_by_role) for role in service.list_roles()]


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(
    response: Response,
    _: Annotated[AuthContext, Depends(require_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PermissionResponse]:
    response.headers["Cache-Control"] = "no-store, private"
    return [
        PermissionResponse(
            id=permission.id,
            key=permission.key,
            name=permission.name,
            sensitive=permission.sensitive,
        )
        for permission in RbacService(db).list_permissions()
    ]


@router.get(
    "/permission-account-directory",
    response_model=list[PermissionAccountDirectoryItem],
)
def list_permission_account_directory(
    response: Response,
    context: Annotated[AuthContext, Depends(require_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PermissionAccountDirectoryItem]:
    response.headers["Cache-Control"] = "no-store, private"
    return [
        PermissionAccountDirectoryItem(
            id=account.id,
            full_name=person.full_name,
            status=cast(AccountStatus, account.status),
        )
        for account, person in RbacService(db).permission_account_directory()
        if _can_manage_account(context, db, account.id)
    ]


@router.get(
    "/permission-person-directory",
    response_model=list[PermissionPersonDirectoryItem],
)
def list_permission_person_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Query(min_length=1, max_length=80)],
) -> list[PermissionPersonDirectoryItem]:
    response.headers["Cache-Control"] = "no-store, private"
    return [
        PermissionPersonDirectoryItem(id=person.id, full_name=person.full_name)
        for person in RbacService(db).permission_person_directory(name.strip())
    ]


@router.get(
    "/permission-building-directory",
    response_model=PermissionBuildingDirectoryPage,
)
def list_permission_building_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionBuildingDirectoryPage:
    rows, total = RbacService(db).permission_building_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionBuildingDirectoryPage(
        items=[
            PermissionBuildingDirectoryItem(
                id=building.id,
                code=building.code,
                name=building.name,
                is_active=building.is_active,
                label=building.name,
            )
            for building in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permission-floor-directory",
    response_model=PermissionFloorDirectoryPage,
)
def list_permission_floor_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionFloorDirectoryPage:
    rows, total = RbacService(db).permission_floor_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionFloorDirectoryPage(
        items=[
            PermissionFloorDirectoryItem(
                id=floor.id,
                building_id=building.id,
                building_name=building.name,
                number=floor.number,
                name=floor.name,
                building_is_active=building.is_active,
                is_active=floor.is_active,
                label=f"{building.name} · {floor.name}",
            )
            for floor, building in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permission-class-directory",
    response_model=PermissionClassDirectoryPage,
)
def list_permission_class_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionClassDirectoryPage:
    rows, total = RbacService(db).permission_class_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionClassDirectoryPage(
        items=[
            PermissionClassDirectoryItem(
                id=teaching_class.id,
                class_name=teaching_class.name,
                cohort_name=cohort.name,
                project_name=project.name,
                label=f"{cohort.name} · {teaching_class.name} · {project.name}",
                is_active=teaching_class.is_active,
            )
            for teaching_class, cohort, project in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permission-cohort-directory",
    response_model=PermissionCohortDirectoryPage,
)
def list_permission_cohort_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionCohortDirectoryPage:
    rows, total = RbacService(db).permission_cohort_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionCohortDirectoryPage(
        items=[
            PermissionCohortDirectoryItem(
                id=cohort.id,
                name=cohort.name,
                status=cast(Any, cohort.status),
                label=cohort.name,
            )
            for cohort in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permission-project-directory",
    response_model=PermissionProjectDirectoryPage,
)
def list_permission_project_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionProjectDirectoryPage:
    rows, total = RbacService(db).permission_project_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionProjectDirectoryPage(
        items=[
            PermissionProjectDirectoryItem(
                id=project.id,
                name=project.name,
                is_active=project.is_active,
                label=project.name,
            )
            for project in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permissions/directory/teams",
    response_model=PermissionTeamDirectoryPage,
)
def list_permission_team_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionTeamDirectoryPage:
    rows, total = RbacService(db).permission_team_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionTeamDirectoryPage(
        items=[
            PermissionTeamDirectoryItem(
                id=team.id,
                name=team.name,
                project_id=project.id,
                project_name=project.name,
                is_active=team.is_active and project.is_active and project.is_team_division,
                label=f"{project.name} · {team.name}",
            )
            for team, project in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/permission-enrollment-directory",
    response_model=PermissionEnrollmentDirectoryPage,
)
def list_permission_enrollment_directory(
    response: Response,
    _: Annotated[AuthContext, Depends(require_global_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PermissionEnrollmentDirectoryPage:
    rows, total = RbacService(db).permission_enrollment_directory(
        query=query,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionEnrollmentDirectoryPage(
        items=[
            PermissionEnrollmentDirectoryItem(
                id=enrollment.id,
                student_name=person.full_name,
                cohort_name=cohort.name,
                class_name=teaching_class.name,
                project_name=project.name,
                status=cast(Any, enrollment.status),
                label=(
                    f"{person.full_name} · {cohort.name} · {teaching_class.name} · {project.name}"
                ),
            )
            for enrollment, person, cohort, teaching_class, project in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/accounts/{account_id}/access", response_model=AccountAccessResponse)
def get_account_access(
    account_id: UUID,
    response: Response,
    _: Annotated[AuthContext, Depends(require_account_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountAccessResponse:
    try:
        service = RbacService(db)
        roles, overrides = service.account_access(account_id)
        effective_permissions = service.account_effective_permissions(account_id)
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="账号不存在") from exc
    response.headers["Cache-Control"] = "no-store, private"
    permissions_by_role = _role_permissions_by_id(db)
    return AccountAccessResponse(
        account_id=account_id,
        roles=[_role_response(role, permissions_by_role) for role in roles],
        permission_overrides=[_override_response(db, override) for override in overrides],
        effective_permissions=[
            EffectivePermissionResponse(
                id=detail.permission.id,
                key=detail.permission.key,
                name=detail.permission.name,
                sensitive=detail.permission.sensitive,
                scope_kinds=list(detail.scope_kinds),
                sources=[
                    cast(Literal["role", "account_override"], source) for source in detail.sources
                ],
            )
            for detail in effective_permissions
        ],
    )


@router.post("/accounts/{account_id}/roles", status_code=204)
def assign_role(
    account_id: UUID,
    input: RoleAssignmentRequest,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(require_global_account_permissions_manager),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            role = RbacService(db).assign_role(
                actor_user_id=actor_user_id,
                account_id=account_id,
                role_id=input.role_id,
            )
            _append_access_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.role.assign",
                account_id=account_id,
                metadata={"role_key": role.key},
            )
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="账号或角色不存在") from exc
    except PermissionDelegationDenied as exc:
        raise ProblemError(status=403, code=exc.code, detail="不能下发超出自身范围的角色") from exc
    return Response(status_code=204)


@router.delete("/accounts/{account_id}/roles/{role_id}", status_code=204)
def remove_role(
    account_id: UUID,
    role_id: UUID,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(require_global_account_permissions_manager),
    ],
    db: Annotated[Session, Depends(get_db)],
    identity: Annotated[IdentityService, Depends(get_identity_service)],
) -> Response:
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            RbacService(db).remove_role(
                actor_user_id=actor_user_id,
                account_id=account_id,
                role_id=role_id,
                request_id=str(request.state.request_id),
                account_readiness=AccountAccessReadinessPolicy(db, cipher=identity.cipher, lookup=identity.lookup),
            )
    except TeamHasActiveRelationships as exc:
        raise ProblemError(status=409, code=exc.code, detail=str(exc)) from exc
    except LastAdminProtected as exc:
        raise ProblemError(
            status=409, code=exc.code, detail="不能移除最后的超级管理员能力"
        ) from exc
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="账号或角色不存在") from exc
    except PermissionDelegationDenied as exc:
        raise ProblemError(status=403, code=exc.code, detail="当前账号无权修改该角色") from exc
    return Response(status_code=204)


@router.post(
    "/accounts/{account_id}/permission-overrides/preview",
    response_model=PermissionPreviewResponse,
)
def preview_permission_override(
    account_id: UUID,
    input: PermissionOverrideCreate,
    response: Response,
    context: Annotated[AuthContext, Depends(require_account_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> PermissionPreviewResponse:
    try:
        decision = RbacService(db).preview_override(
            actor_user_id=context.account.id,
            account_id=account_id,
            input=input,
        )
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="账号、权限或范围对象不存在") from exc
    except InactivePermissionScope as exc:
        raise ProblemError(
            status=409, code=exc.code, detail="停用的范围对象不能新增允许规则"
        ) from exc
    except InvalidPermissionOverride as exc:
        raise ProblemError(status=422, code=exc.code, detail="权限覆盖规则不符合要求") from exc
    except PermissionDelegationDenied as exc:
        raise ProblemError(status=403, code=exc.code, detail="当前账号无权维护该权限") from exc
    response.headers["Cache-Control"] = "no-store, private"
    return PermissionPreviewResponse(allowed=decision.allowed, reason=decision.reason)


@router.post(
    "/accounts/{account_id}/permission-overrides",
    response_model=PermissionOverrideResponse,
    status_code=201,
)
def add_permission_override(
    account_id: UUID,
    input: PermissionOverrideCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_account_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> PermissionOverrideResponse:
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            override, permission = RbacService(db).add_override(
                actor_user_id=actor_user_id,
                account_id=account_id,
                input=input,
            )
            _append_access_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.permission_override.add",
                account_id=account_id,
                metadata=_override_metadata(
                    permission_key=permission.key,
                    effect=override.effect,
                    scope_kind=override.scope_kind,
                    scope_id=override.scope_id,
                ),
            )
    except SelfPermissionLockout as exc:
        raise ProblemError(status=409, code=exc.code, detail="不能拒绝自己的权限管理能力") from exc
    except LastAdminProtected as exc:
        raise ProblemError(
            status=409, code=exc.code, detail="不能移除最后的超级管理员能力"
        ) from exc
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="账号或权限不存在") from exc
    except InactivePermissionScope as exc:
        raise ProblemError(
            status=409, code=exc.code, detail="停用的范围对象不能新增允许规则"
        ) from exc
    except InvalidPermissionOverride as exc:
        raise ProblemError(status=422, code=exc.code, detail="权限覆盖规则不符合要求") from exc
    except PermissionDelegationDenied as exc:
        raise ProblemError(status=403, code=exc.code, detail="不能下发超出自身范围的权限") from exc
    except DuplicatePermissionRule as exc:
        raise ProblemError(status=409, code=exc.code, detail="相同权限规则已存在") from exc
    return _override_response(db, override)


@router.delete(
    "/accounts/{account_id}/permission-overrides/{override_id}",
    status_code=204,
)
def remove_permission_override(
    account_id: UUID,
    override_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_account_permissions_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():

            def append_audit(change: PermissionOverrideChange) -> None:
                _append_access_audit(
                    db=db,
                    request=request,
                    actor_user_id=actor_user_id,
                    action="account.permission_override.remove",
                    account_id=account_id,
                    metadata=_override_metadata(
                        permission_key=change.permission_key,
                        effect=change.effect,
                        scope_kind=change.scope_kind,
                        scope_id=change.scope_id,
                    ),
                )

            RbacService(db).remove_override(
                actor_user_id=actor_user_id,
                account_id=account_id,
                override_id=override_id,
                before_delete=append_audit,
            )
    except SelfPermissionLockout as exc:
        raise ProblemError(status=409, code=exc.code, detail="不能移除自己的权限管理能力") from exc
    except LastAdminProtected as exc:
        raise ProblemError(
            status=409, code=exc.code, detail="不能移除最后的超级管理员能力"
        ) from exc
    except RbacResourceNotFound as exc:
        raise ProblemError(status=404, code=exc.code, detail="权限覆盖规则不存在") from exc
    except PermissionDelegationDenied as exc:
        raise ProblemError(
            status=403, code=exc.code, detail="不能移除超出自身范围的拒绝规则"
        ) from exc
    return Response(status_code=204)
