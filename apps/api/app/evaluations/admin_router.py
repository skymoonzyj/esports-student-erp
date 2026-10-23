from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import ColumnElement, SQLColumnExpression, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationResource,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.dependencies import (
    get_analysis_profile_service,
    get_evaluation_context_service,
    get_evaluation_media_service,
    get_game_account_service,
    get_staff_service,
)
from app.evaluations.errors import (
    AnalysisAdapterNotAccepted,
    AnalysisProfileError,
    AnalysisProfileVersionConflict,
    AuxiliaryPhoneConflict,
    EvaluationGameAccountError,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationStaffServiceError,
    GameAccountLimitInvalid,
    InvalidAnalysisProfile,
    StaffAssignmentAlreadyEnded,
    StaffAssignmentConflict,
    StaffAssignmentNotFound,
    StaffRoleNotFound,
)
from app.evaluations.game_account_service import GameAccountService
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.portal_router import require_evaluation_center
from app.evaluations.schemas import (
    AnalysisProfileCreate,
    AnalysisProfileItem,
    AuxiliaryPhoneCreate,
    AuxiliaryPhoneDeactivate,
    AuxiliaryPhoneItem,
    AuxiliaryPhoneReplace,
    EvaluationCohortDirectoryItem,
    EvaluationCohortDirectoryPage,
    EvaluationProjectDirectoryItem,
    EvaluationProjectDirectoryPage,
    EvaluationStaffScopeDirectoryItem,
    EvaluationStaffScopeDirectoryPage,
    GameAccountLimitResponse,
    GameAccountLimitUpdate,
    MediaStorageStatusResponse,
    StaffAssignmentCreate,
    StaffAssignmentEnd,
    StaffDirectoryItem,
    StaffDirectoryPage,
    StaffRoleCreate,
    StaffRoleItem,
    StaffServiceItem,
    StudentDirectoryItem,
    StudentDirectoryPage,
    StudentMatchingProfileResponse,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.evaluations.staff_service import CohortStaffService
from app.identity.models import Person, StaffProfile, StudentProfile
from app.rbac.models import Permission
from app.teaching.errors import CohortArchived, CohortNotFound
from app.teaching.models import Cohort, GameProject, TeachingClass

router = APIRouter(prefix="/evaluations/admin", tags=["evaluation administration"])


def _operation_denied() -> NoReturn:
    raise ProblemError(
        status=403,
        code="evaluation_operation_denied",
        detail="没有执行此评价操作的权限",
    )


def _private_resource_not_found() -> NoReturn:
    raise ProblemError(
        status=404,
        code="evaluation_resource_not_found",
        detail="评价资源不存在",
    )


def _raise_game_admin_error(
    exc: EvaluationGameAccountError,
    *,
    private_object: bool = False,
) -> NoReturn:
    if isinstance(exc, EvaluationResourceNotFound):
        error = (404, "evaluation_resource_not_found", "评价资源不存在")
    elif isinstance(exc, EvaluationOperationDenied):
        error = (
            (404, "evaluation_resource_not_found", "评价资源不存在")
            if private_object
            else (403, "evaluation_operation_denied", "没有执行此评价操作的权限")
        )
    elif isinstance(exc, GameAccountLimitInvalid):
        error = (409, "game_account_limit_invalid", "游戏账号限额冲突")
    elif isinstance(exc, AuxiliaryPhoneConflict):
        error = (409, "auxiliary_phone_conflict", "辅助手机号状态冲突")
    else:
        error = (422, exc.code, "评价数据请求无效")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


def _require_operation(
    db: Session,
    *,
    account_id: UUID,
    permission: str,
    occurred_at: datetime,
    resource: EvaluationResource | None = None,
) -> None:
    decision = EvaluationAccessService(db).decide(
        account_id=account_id,
        permission=permission,
        resource=resource or EvaluationResource(),
        occurred_at=occurred_at,
    )
    if not decision.allowed:
        _operation_denied()


def _require_private_object_access(
    db: Session,
    *,
    account_id: UUID,
    permission: str,
    occurred_at: datetime,
    resource: EvaluationResource,
) -> None:
    if not EvaluationAccessService(db).decide(
        account_id=account_id,
        permission=permission,
        resource=resource,
        occurred_at=occurred_at,
    ).allowed:
        _private_resource_not_found()


def _require_staff_foundation_read(
    db: Session,
    *,
    context_service: EvaluationContextService,
    account_id: UUID,
    occurred_at: datetime,
) -> None:
    globally_allowed = (
        EvaluationAccessService(db)
        .decide(
            account_id=account_id,
            permission="cohort_staff.manage",
            resource=EvaluationResource(),
            occurred_at=occurred_at,
        )
        .allowed
    )
    if globally_allowed:
        return
    if context_service.admin_sections(
        account_id=account_id,
        occurred_at=occurred_at,
    ).cohort_staff:
        return
    _operation_denied()


def _require_analysis_foundation_read(
    db: Session,
    *,
    context_service: EvaluationContextService,
    account_id: UUID,
    occurred_at: datetime,
) -> None:
    globally_allowed = (
        EvaluationAccessService(db)
        .decide(
            account_id=account_id,
            permission="analysis_profiles.manage",
            resource=EvaluationResource(),
            occurred_at=occurred_at,
        )
        .allowed
    )
    if globally_allowed:
        return
    if context_service.admin_sections(
        account_id=account_id,
        occurred_at=occurred_at,
    ).analysis_profiles:
        return
    _operation_denied()


def _authorized_student_row(
    db: Session,
    *,
    account_id: UUID,
    student_id: UUID,
    occurred_at: datetime,
) -> tuple[Person, StudentProfile]:
    authorization = EvaluationAccessService(db).sql_context(
        account_id=account_id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    row = db.execute(
        select(Person, StudentProfile)
        .join(StudentProfile, StudentProfile.person_id == Person.id)
        .where(
            Person.id == student_id,
            Person.deleted_at.is_(None),
            authorization.allowed(
                EvaluationSqlResource(student_person_id=Person.id)
            ),
        )
    ).one_or_none()
    if row is None:
        _private_resource_not_found()
    return row[0], row[1]


def _require_administrator_student(
    db: Session,
    *,
    account_id: UUID,
    student_id: UUID,
    occurred_at: datetime,
) -> tuple[Person, StudentProfile]:
    _require_operation(
        db,
        account_id=account_id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    return _authorized_student_row(
        db,
        account_id=account_id,
        student_id=student_id,
        occurred_at=occurred_at,
    )


def _require_authorized_analysis_project(
    db: Session,
    *,
    account_id: UUID,
    project_id: UUID,
    occurred_at: datetime,
) -> None:
    authorization = EvaluationAccessService(db).sql_context(
        account_id=account_id,
        permission="analysis_profiles.manage",
        occurred_at=occurred_at,
    )
    if db.scalar(
        select(GameProject.id)
        .where(
            GameProject.id == project_id,
            authorization.allowed(
                EvaluationSqlResource(project_id=GameProject.id)
            ),
        )
        .limit(1)
    ) is None:
        _private_resource_not_found()


def _limit_response(
    db: Session,
    *,
    student_id: UUID,
    profile: StudentProfile,
) -> GameAccountLimitResponse:
    active_count = (
        db.scalar(
            select(func.count(StudentGameAccount.id)).where(
                StudentGameAccount.student_person_id == student_id,
                StudentGameAccount.unbound_at.is_(None),
            )
        )
        or 0
    )
    return GameAccountLimitResponse(
        student_person_id=student_id,
        active_count=active_count,
        active_limit=profile.game_account_limit,
    )


def _phone_item(
    service: GameAccountService,
    *,
    student_id: UUID,
    phone_id: UUID,
    actor_user_id: UUID,
    occurred_at: datetime,
) -> AuxiliaryPhoneItem:
    _require_operation(
        service.db,
        account_id=actor_user_id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    _require_private_object_access(
        service.db,
        account_id=actor_user_id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
        resource=EvaluationResource(student_person_id=student_id),
    )
    phone = service.db.scalar(
        select(StudentAuxiliaryPhone).where(
            StudentAuxiliaryPhone.id == phone_id,
            StudentAuxiliaryPhone.student_person_id == student_id,
        )
    )
    if phone is None:
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    raw_phone = service.cipher.decrypt(
        phone.phone_ciphertext,
        field_name="student_auxiliary_phone",
    )
    return AuxiliaryPhoneItem(
        id=phone.id,
        masked_phone=f"{raw_phone[:3]}****{raw_phone[-4:]}",
        status="inactive" if phone.disabled_at is not None else "active",
        created_at=phone.created_at,
        disabled_at=phone.disabled_at,
    )


@router.get("/students", response_model=StudentDirectoryPage)
def list_students(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str, Query(max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StudentDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_operation(
        db,
        account_id=context.account.id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    filters: list[ColumnElement[bool]] = [Person.deleted_at.is_(None)]
    normalized_query = query.strip()
    if normalized_query:
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                Person.full_name.ilike(pattern, escape="\\"),
                StudentProfile.student_number.ilike(pattern, escape="\\"),
            )
        )
    access = EvaluationAccessService(db)
    authorization = access.sql_context(
        account_id=context.account.id,
        permission="student_game_accounts.manage",
        occurred_at=occurred_at,
    )
    statement = (
        select(Person, StudentProfile)
        .join(StudentProfile, StudentProfile.person_id == Person.id)
        .where(
            *filters,
            authorization.allowed(
                EvaluationSqlResource(student_person_id=Person.id)
            ),
        )
    )
    total = db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    rows = db.execute(
        statement.order_by(StudentProfile.student_number, Person.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return StudentDirectoryPage(
        items=[
            StudentDirectoryItem(
                student_person_id=person.id,
                student_name=person.full_name,
                student_number=profile.student_number,
            )
            for person, profile in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/students/{student_id}/game-account-limit",
    response_model=GameAccountLimitResponse,
)
def get_game_account_limit(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> GameAccountLimitResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _person, profile = _require_administrator_student(
        db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    return _limit_response(db, student_id=student_id, profile=profile)


@router.put(
    "/students/{student_id}/game-account-limit",
    response_model=GameAccountLimitResponse,
)
def update_game_account_limit(
    student_id: UUID,
    input: GameAccountLimitUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
    db: Annotated[Session, Depends(get_db)],
) -> GameAccountLimitResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_administrator_student(
        db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        profile = service.update_game_account_limit(
            student_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)
    return _limit_response(db, student_id=student_id, profile=profile)


@router.get(
    "/students/{student_id}/auxiliary-phones",
    response_model=list[AuxiliaryPhoneItem],
)
def list_auxiliary_phones(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
    include_inactive: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[AuxiliaryPhoneItem]:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_administrator_student(
        service.db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        return service.list_auxiliary_phones(
            student_id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)


@router.post(
    "/students/{student_id}/auxiliary-phones",
    response_model=AuxiliaryPhoneItem,
    status_code=201,
)
def add_auxiliary_phone(
    student_id: UUID,
    input: AuxiliaryPhoneCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
) -> AuxiliaryPhoneItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_administrator_student(
        service.db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        phone = service.add_auxiliary_phone(
            student_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _phone_item(
            service,
            student_id=student_id,
            phone_id=phone.id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)


@router.post(
    "/students/{student_id}/auxiliary-phones/{phone_id}/replace",
    response_model=AuxiliaryPhoneItem,
    status_code=201,
)
def replace_auxiliary_phone(
    student_id: UUID,
    phone_id: UUID,
    input: AuxiliaryPhoneReplace,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
) -> AuxiliaryPhoneItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_administrator_student(
        service.db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        phone = service.replace_auxiliary_phone(
            student_id,
            phone_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _phone_item(
            service,
            student_id=student_id,
            phone_id=phone.id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)


@router.post(
    "/students/{student_id}/auxiliary-phones/{phone_id}/deactivate",
    response_model=AuxiliaryPhoneItem,
)
def deactivate_auxiliary_phone(
    student_id: UUID,
    phone_id: UUID,
    input: AuxiliaryPhoneDeactivate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
) -> AuxiliaryPhoneItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_administrator_student(
        service.db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        phone = service.deactivate_auxiliary_phone(
            student_id,
            phone_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _phone_item(
            service,
            student_id=student_id,
            phone_id=phone.id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)


@router.get(
    "/students/{student_id}/matching-profile",
    response_model=StudentMatchingProfileResponse,
)
def get_matching_profile(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[GameAccountService, Depends(get_game_account_service)],
    db: Annotated[Session, Depends(get_db)],
) -> StudentMatchingProfileResponse:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    person, profile = _require_administrator_student(
        db,
        account_id=context.account.id,
        student_id=student_id,
        occurred_at=occurred_at,
    )
    try:
        history_preview_size = 50
        accounts, account_total = service.list_game_accounts_page(
            student_id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            include_unbound=True,
            page=1,
            page_size=history_preview_size,
        )
        phones, phone_total = service.list_auxiliary_phones_page(
            student_id,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            include_inactive=True,
            page=1,
            page_size=history_preview_size,
        )
    except EvaluationGameAccountError as exc:
        _raise_game_admin_error(exc, private_object=True)
    limit = _limit_response(db, student_id=student_id, profile=profile)
    return StudentMatchingProfileResponse(
        student_person_id=student_id,
        student_name=person.full_name,
        student_number=profile.student_number,
        active_count=limit.active_count,
        active_limit=limit.active_limit,
        game_accounts=accounts,
        game_accounts_total=account_total,
        game_accounts_truncated=account_total > len(accounts),
        auxiliary_phones=phones,
        auxiliary_phones_total=phone_total,
        auxiliary_phones_truncated=phone_total > len(phones),
    )


def _staff_role_item(role: EvaluationStaffRole) -> StaffRoleItem:
    return StaffRoleItem(
        id=role.id,
        key=role.key,
        name=role.name,
        category=cast(
            Literal["teaching", "life_service", "operations"],
            role.category,
        ),
        is_builtin=role.is_builtin,
        is_active=role.is_active,
    )


def _raise_staff_error(exc: Exception) -> NoReturn:
    if isinstance(exc, (StaffRoleNotFound, StaffAssignmentNotFound, CohortNotFound)):
        error = (404, "evaluation_resource_not_found", "评价资源不存在")
    elif isinstance(exc, CohortArchived):
        error = (409, exc.code, "归档营期不可修改工作人员服务")
    elif isinstance(exc, StaffAssignmentConflict):
        error = (409, "staff_assignment_conflict", "工作人员服务时间冲突")
    elif isinstance(exc, StaffAssignmentAlreadyEnded):
        error = (409, "staff_assignment_already_ended", "工作人员服务已经结束")
    elif isinstance(exc, IntegrityError):
        error = (409, "staff_role_conflict", "工作人员角色冲突")
    elif isinstance(exc, EvaluationStaffServiceError):
        error = (422, exc.code, "工作人员服务请求无效")
    else:
        error = (500, "evaluation_staff_service_error", "工作人员服务暂时不可用")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


def _staff_history(
    db: Session,
    service: CohortStaffService,
    *,
    cohort_id: UUID,
    occurred_at: datetime,
) -> list[StaffServiceItem]:
    items = service.list_service_history(cohort_id, occurred_at=occurred_at)
    generic_ids = {item.id for item in items if item.source == "cohort_staff_assignment"}
    permission_keys: dict[UUID, list[str]] = {item_id: [] for item_id in generic_ids}
    if generic_ids:
        for assignment_id, permission_key in db.execute(
            select(
                CohortStaffAssignmentPermission.assignment_id,
                Permission.key,
            )
            .join(
                Permission,
                Permission.id == CohortStaffAssignmentPermission.permission_id,
            )
            .where(CohortStaffAssignmentPermission.assignment_id.in_(generic_ids))
            .order_by(Permission.key)
        ):
            permission_keys[assignment_id].append(permission_key)
    return [
        item.model_copy(update={"permission_keys": permission_keys.get(item.id, [])})
        for item in items
    ]


def _staff_history_item(
    db: Session,
    service: CohortStaffService,
    *,
    cohort_id: UUID,
    assignment_id: UUID,
    occurred_at: datetime,
) -> StaffServiceItem:
    for item in _staff_history(
        db,
        service,
        cohort_id=cohort_id,
        occurred_at=occurred_at,
    ):
        if item.id == assignment_id:
            return item
    raise ProblemError(
        status=404,
        code="evaluation_resource_not_found",
        detail="评价资源不存在",
    )


def _cohort_staff_resources(
    db: Session,
    *,
    cohort_id: UUID,
) -> list[EvaluationResource]:
    if db.get(Cohort, cohort_id) is None:
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    class_rows = list(
        db.execute(
            select(TeachingClass.id, TeachingClass.project_id).where(
                TeachingClass.cohort_id == cohort_id
            )
        )
    )
    project_ids = list(dict.fromkeys(project_id for _class_id, project_id in class_rows))
    resources = [
        EvaluationResource(cohort_id=cohort_id),
        *(
            EvaluationResource(cohort_id=cohort_id, project_id=project_id)
            for project_id in project_ids
        ),
    ]
    resources.extend(
        EvaluationResource(
            cohort_id=cohort_id,
            project_id=project_id,
            class_id=class_id,
        )
        for class_id, project_id in class_rows
    )
    return resources


def _require_any_cohort_staff_scope(
    db: Session,
    *,
    account_id: UUID,
    cohort_id: UUID,
    occurred_at: datetime,
) -> EvaluationAccessService:
    access = EvaluationAccessService(db)
    allowed = access.filter_resources(
        account_id=account_id,
        permission="cohort_staff.manage",
        resources=_cohort_staff_resources(db, cohort_id=cohort_id),
        occurred_at=occurred_at,
    )
    if not allowed:
        _private_resource_not_found()
    return access


def _eligible_staff_statement(
    *,
    staff_allowed: ColumnElement[bool],
) -> Select[tuple[Person, StaffProfile]]:
    return (
        select(Person, StaffProfile)
        .join(StaffProfile, StaffProfile.person_id == Person.id)
        .where(
            Person.deleted_at.is_(None),
            select(UserAccount.id)
            .where(
                UserAccount.person_id == Person.id,
                UserAccount.status == "active",
            )
            .exists(),
            staff_allowed,
        )
    )


def _staff_scope_sql_allowed(
    authorization: EvaluationSqlContext,
    *,
    cohort_id: SQLColumnExpression[UUID],
    project_id: SQLColumnExpression[UUID],
    class_id: SQLColumnExpression[UUID] | None = None,
) -> ColumnElement[bool]:
    return authorization.allowed(
        EvaluationSqlResource(
            cohort_id=cohort_id,
            project_id=project_id,
            class_id=class_id,
        )
    )


def _authorized_staff_assignment_input_resource(
    db: Session,
    *,
    account_id: UUID,
    cohort_id: UUID,
    input: StaffAssignmentCreate,
    occurred_at: datetime,
) -> EvaluationResource:
    authorization = EvaluationAccessService(db).sql_context(
        account_id=account_id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
    )
    cohort_value = literal(cohort_id)
    staff = db.execute(
        _eligible_staff_statement(
            staff_allowed=_cohort_staff_sql_allowed(
                authorization,
                cohort_id=cohort_value,
                staff_person_id=Person.id,
            )
        )
        .where(Person.id == input.staff_person_id)
        .limit(1)
    ).one_or_none()
    if staff is None:
        _private_resource_not_found()

    if input.scope_kind == "project":
        project_id = db.scalar(
            select(GameProject.id)
            .join(TeachingClass, TeachingClass.project_id == GameProject.id)
            .where(
                GameProject.id == input.scope_id,
                TeachingClass.cohort_id == cohort_id,
                _staff_scope_sql_allowed(
                    authorization,
                    cohort_id=cohort_value,
                    project_id=GameProject.id,
                ),
            )
            .distinct()
            .limit(1)
        )
        if project_id is None:
            _private_resource_not_found()
        return EvaluationResource(
            staff_person_id=input.staff_person_id,
            cohort_id=cohort_id,
            project_id=project_id,
        )
    class_row = db.execute(
        select(TeachingClass.project_id, TeachingClass.id)
        .join(GameProject, GameProject.id == TeachingClass.project_id)
        .where(
            TeachingClass.id == input.scope_id,
            TeachingClass.cohort_id == cohort_id,
            _staff_scope_sql_allowed(
                authorization,
                cohort_id=cohort_value,
                project_id=TeachingClass.project_id,
                class_id=TeachingClass.id,
            ),
        )
        .limit(1)
    ).one_or_none()
    if class_row is None:
        _private_resource_not_found()
    return EvaluationResource(
        staff_person_id=input.staff_person_id,
        cohort_id=cohort_id,
        project_id=class_row.project_id,
        class_id=class_row.id,
    )


def _staff_assignment_resource(
    db: Session,
    assignment: CohortStaffAssignment,
) -> EvaluationResource:
    project_id = assignment.project_id
    if project_id is None and assignment.class_id is not None:
        project_id = db.scalar(
            select(TeachingClass.project_id).where(TeachingClass.id == assignment.class_id)
        )
    return EvaluationResource(
        staff_person_id=assignment.staff_person_id,
        cohort_id=assignment.cohort_id,
        project_id=project_id,
        class_id=assignment.class_id,
    )


def _staff_item_resource(
    item: StaffServiceItem,
    *,
    cohort_id: UUID,
) -> EvaluationResource:
    return EvaluationResource(
        staff_person_id=item.staff_person_id,
        cohort_id=cohort_id,
        project_id=item.project_id,
        class_id=item.class_id,
    )


def _cohort_staff_sql_allowed(
    authorization: EvaluationSqlContext,
    *,
    cohort_id: SQLColumnExpression[UUID],
    staff_person_id: SQLColumnExpression[UUID] | None = None,
) -> ColumnElement[bool]:
    cohort_allowed = authorization.allowed(
        EvaluationSqlResource(
            staff_person_id=staff_person_id,
            cohort_id=cohort_id,
        )
    )
    class_scope_allowed = (
        select(TeachingClass.id)
        .where(
            TeachingClass.cohort_id == cohort_id,
            or_(
                authorization.allowed(
                    EvaluationSqlResource(
                        staff_person_id=staff_person_id,
                        cohort_id=cohort_id,
                        project_id=TeachingClass.project_id,
                    )
                ),
                authorization.allowed(
                    EvaluationSqlResource(
                        staff_person_id=staff_person_id,
                        cohort_id=cohort_id,
                        project_id=TeachingClass.project_id,
                        class_id=TeachingClass.id,
                    )
                ),
            ),
        )
        .exists()
    )
    return or_(cohort_allowed, class_scope_allowed)


@router.get("/cohorts", response_model=EvaluationCohortDirectoryPage)
def list_staff_cohorts(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str, Query(max_length=120)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationCohortDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    authorization = EvaluationAccessService(db).sql_context(
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
    )
    filters: list[ColumnElement[bool]] = []
    normalized_query = query.strip()
    if normalized_query:
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Cohort.name.ilike(f"%{escaped}%", escape="\\"))
    statement = select(Cohort).where(
        *filters,
        _cohort_staff_sql_allowed(authorization, cohort_id=Cohort.id),
    )
    total = db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    cohorts = db.scalars(
        statement.order_by(Cohort.starts_at.desc(), Cohort.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return EvaluationCohortDirectoryPage(
        items=[
            EvaluationCohortDirectoryItem(id=cohort.id, name=cohort.name)
            for cohort in cohorts
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/cohorts/{cohort_id}/staff-scopes",
    response_model=EvaluationStaffScopeDirectoryPage,
)
def list_staff_scopes(
    cohort_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    scope_kind: Annotated[Literal["project", "class"], Query()],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationStaffScopeDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    authorization = EvaluationAccessService(db).sql_context(
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
    )
    if db.scalar(
        select(Cohort.id)
        .where(
            Cohort.id == cohort_id,
            _cohort_staff_sql_allowed(authorization, cohort_id=Cohort.id),
        )
        .limit(1)
    ) is None:
        _private_resource_not_found()

    cohort_value = literal(cohort_id)
    if scope_kind == "project":
        project_statement = (
            select(GameProject.id, GameProject.name, GameProject.is_active)
            .join(TeachingClass, TeachingClass.project_id == GameProject.id)
            .where(
                TeachingClass.cohort_id == cohort_id,
                _staff_scope_sql_allowed(
                    authorization,
                    cohort_id=cohort_value,
                    project_id=GameProject.id,
                ),
            )
            .distinct()
        )
        total = db.scalar(
            select(func.count()).select_from(
                project_statement.order_by(None).subquery()
            )
        ) or 0
        rows = db.execute(
            project_statement.order_by(GameProject.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            EvaluationStaffScopeDirectoryItem(
                id=project_id,
                name=project_name,
                project_id=project_id,
                project_name=project_name,
                is_active=is_active,
            )
            for project_id, project_name, is_active in rows
        ]
    else:
        class_statement = (
            select(TeachingClass, GameProject)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(
                TeachingClass.cohort_id == cohort_id,
                _staff_scope_sql_allowed(
                    authorization,
                    cohort_id=cohort_value,
                    project_id=TeachingClass.project_id,
                    class_id=TeachingClass.id,
                ),
            )
        )
        total = db.scalar(
            select(func.count()).select_from(
                class_statement.order_by(None).subquery()
            )
        ) or 0
        rows = db.execute(
            class_statement.order_by(TeachingClass.name, TeachingClass.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            EvaluationStaffScopeDirectoryItem(
                id=teaching_class.id,
                name=teaching_class.name,
                project_id=project.id,
                project_name=project.name,
                is_active=teaching_class.is_active,
            )
            for teaching_class, project in rows
        ]

    return EvaluationStaffScopeDirectoryPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/staff-directory", response_model=StaffDirectoryPage)
def list_staff_directory(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    cohort_id: UUID | None = None,
    query: Annotated[str, Query(max_length=100)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StaffDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    access = EvaluationAccessService(db)
    authorization = access.sql_context(
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
    )
    globally_allowed = bool(
        db.scalar(select(authorization.allowed(EvaluationSqlResource())))
    )
    if cohort_id is not None:
        if db.scalar(
            select(Cohort.id)
            .where(
                Cohort.id == cohort_id,
                _cohort_staff_sql_allowed(authorization, cohort_id=Cohort.id),
            )
            .limit(1)
        ) is None:
            _private_resource_not_found()
        staff_allowed = _cohort_staff_sql_allowed(
            authorization,
            cohort_id=literal(cohort_id),
            staff_person_id=Person.id,
        )
    elif not globally_allowed:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="专项工作人员授权必须指定营期",
        )
    else:
        staff_allowed = authorization.allowed(
            EvaluationSqlResource(staff_person_id=Person.id)
        )
    statement = _eligible_staff_statement(staff_allowed=staff_allowed)
    normalized_query = query.strip()
    if normalized_query:
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            Person.full_name.ilike(f"%{escaped}%", escape="\\")
        )
    total = db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    rows = db.execute(
        statement.order_by(Person.full_name, Person.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return StaffDirectoryPage(
        items=[
            StaffDirectoryItem(
                staff_person_id=person.id,
                staff_name=person.full_name,
                staff_type=cast(
                    Literal[
                        "coach",
                        "life_teacher",
                        "customer_service",
                        "administration",
                        "other",
                    ],
                    profile.staff_type,
                ),
            )
            for person, profile in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/staff-roles", response_model=list[StaffRoleItem])
def list_staff_roles(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> list[StaffRoleItem]:
    require_evaluation_center(context=context, service=context_service)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=datetime.now(UTC),
    )
    return [
        _staff_role_item(role)
        for role in db.scalars(
            select(EvaluationStaffRole).order_by(
                EvaluationStaffRole.is_builtin.desc(),
                EvaluationStaffRole.name,
                EvaluationStaffRole.id,
            )
        )
    ]


@router.post("/staff-roles", response_model=StaffRoleItem, status_code=201)
def create_staff_role(
    input: StaffRoleCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[CohortStaffService, Depends(get_staff_service)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffRoleItem:
    require_evaluation_center(context=context, service=context_service)
    _require_operation(
        db,
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=datetime.now(UTC),
    )
    try:
        return _staff_role_item(
            service.create_role(
                input,
                actor_user_id=context.account.id,
                request_id=str(request.state.request_id),
            )
        )
    except (EvaluationStaffServiceError, IntegrityError) as exc:
        _raise_staff_error(exc)


@router.post(
    "/staff-roles/{role_id}/deactivate",
    response_model=StaffRoleItem,
)
def deactivate_staff_role(
    role_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[CohortStaffService, Depends(get_staff_service)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffRoleItem:
    require_evaluation_center(context=context, service=context_service)
    _require_operation(
        db,
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=datetime.now(UTC),
    )
    try:
        return _staff_role_item(
            service.set_role_active(
                role_id,
                is_active=False,
                actor_user_id=context.account.id,
                request_id=str(request.state.request_id),
            )
        )
    except EvaluationStaffServiceError as exc:
        _raise_staff_error(exc)


@router.get(
    "/cohorts/{cohort_id}/staff-assignments",
    response_model=list[StaffServiceItem],
)
def list_staff_assignments(
    cohort_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[CohortStaffService, Depends(get_staff_service)],
    db: Annotated[Session, Depends(get_db)],
) -> list[StaffServiceItem]:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    access = _require_any_cohort_staff_scope(
        db,
        account_id=context.account.id,
        cohort_id=cohort_id,
        occurred_at=occurred_at,
    )
    try:
        items = _staff_history(
            db,
            service,
            cohort_id=cohort_id,
            occurred_at=occurred_at,
        )
        item_resources = [
            (item, _staff_item_resource(item, cohort_id=cohort_id)) for item in items
        ]
        allowed_resources = set(
            access.filter_resources(
                account_id=context.account.id,
                permission="cohort_staff.manage",
                resources=[resource for _item, resource in item_resources],
                occurred_at=occurred_at,
            )
        )
        return [
            item
            for item, resource in item_resources
            if resource in allowed_resources
        ]
    except (EvaluationStaffServiceError, CohortNotFound) as exc:
        _raise_staff_error(exc)


@router.post(
    "/cohorts/{cohort_id}/staff-assignments",
    response_model=StaffServiceItem,
    status_code=201,
)
def create_staff_assignment(
    cohort_id: UUID,
    input: StaffAssignmentCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[CohortStaffService, Depends(get_staff_service)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffServiceItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    _require_any_cohort_staff_scope(
        db,
        account_id=context.account.id,
        cohort_id=cohort_id,
        occurred_at=occurred_at,
    )
    assignment_resource = _authorized_staff_assignment_input_resource(
        db,
        account_id=context.account.id,
        cohort_id=cohort_id,
        input=input,
        occurred_at=occurred_at,
    )
    _require_private_object_access(
        db,
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
        resource=assignment_resource,
    )
    try:
        assignment = service.create_assignment(
            cohort_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
        return _staff_history_item(
            db,
            service,
            cohort_id=cohort_id,
            assignment_id=assignment.id,
            occurred_at=occurred_at,
        )
    except (EvaluationStaffServiceError, CohortNotFound, CohortArchived) as exc:
        _raise_staff_error(exc)


@router.post(
    "/staff-assignments/{assignment_id}/end",
    response_model=StaffServiceItem,
)
def end_staff_assignment(
    assignment_id: UUID,
    input: StaffAssignmentEnd,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[CohortStaffService, Depends(get_staff_service)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffServiceItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_staff_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    assignment_row = db.get(CohortStaffAssignment, assignment_id)
    if assignment_row is None:
        _private_resource_not_found()
    cohort_id = assignment_row.cohort_id
    _require_private_object_access(
        db,
        account_id=context.account.id,
        permission="cohort_staff.manage",
        occurred_at=occurred_at,
        resource=_staff_assignment_resource(db, assignment_row),
    )
    try:
        assignment = service.end_assignment(
            cohort_id,
            assignment_id,
            input,
            actor_user_id=context.account.id,
            occurred_at=occurred_at,
            request_id=str(request.state.request_id),
        )
        return _staff_history_item(
            db,
            service,
            cohort_id=cohort_id,
            assignment_id=assignment.id,
            occurred_at=occurred_at,
        )
    except (EvaluationStaffServiceError, CohortArchived) as exc:
        _raise_staff_error(exc)


def _raise_analysis_error(
    exc: AnalysisProfileError | EvaluationResourceNotFound,
) -> NoReturn:
    if isinstance(exc, EvaluationResourceNotFound):
        error = (404, "evaluation_resource_not_found", "评价资源不存在")
    elif isinstance(exc, AnalysisProfileVersionConflict):
        error = (409, "analysis_profile_version_conflict", "分析配置版本已经变化")
    elif isinstance(exc, AnalysisAdapterNotAccepted):
        error = (409, "analysis_adapter_not_accepted", "自动分析适配器尚未验收")
    elif isinstance(exc, InvalidAnalysisProfile):
        error = (422, "invalid_analysis_profile", "分析配置请求无效")
    else:
        error = (422, exc.code, "分析配置请求无效")
    raise ProblemError(status=error[0], code=error[1], detail=error[2]) from exc


@router.get("/projects", response_model=EvaluationProjectDirectoryPage)
def list_analysis_projects(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str, Query(max_length=80)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationProjectDirectoryPage:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    authorization = EvaluationAccessService(db).sql_context(
        account_id=context.account.id,
        permission="analysis_profiles.manage",
        occurred_at=occurred_at,
    )
    filters: list[ColumnElement[bool]] = []
    normalized_query = query.strip()
    if normalized_query:
        escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(GameProject.name.ilike(f"%{escaped}%", escape="\\"))
    statement = select(GameProject).where(
        *filters,
        authorization.allowed(EvaluationSqlResource(project_id=GameProject.id)),
    )
    total = db.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    projects = db.scalars(
        statement.order_by(GameProject.sort_order, GameProject.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return EvaluationProjectDirectoryPage(
        items=[
            EvaluationProjectDirectoryItem(
                id=project.id,
                name=project.name,
                is_active=project.is_active,
            )
            for project in projects
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _analysis_profile_items(
    db: Session,
    service: AnalysisProfileService,
    *,
    project_id: UUID,
    occurred_at: datetime,
) -> list[AnalysisProfileItem]:
    rows = list(
        db.execute(
            select(ProjectAnalysisProfile, GameProject, Person.full_name)
            .join(GameProject, GameProject.id == ProjectAnalysisProfile.project_id)
            .outerjoin(
                UserAccount,
                UserAccount.id == ProjectAnalysisProfile.configured_by_user_id,
            )
            .outerjoin(Person, Person.id == UserAccount.person_id)
            .where(ProjectAnalysisProfile.project_id == project_id)
            .order_by(ProjectAnalysisProfile.version.desc())
        )
    )
    if not rows:
        raise ProblemError(
            status=404,
            code="evaluation_resource_not_found",
            detail="评价资源不存在",
        )
    current = service.current_at(project_id, occurred_at=occurred_at)
    return [
        AnalysisProfileItem(
            id=profile.id,
            project_id=project.id,
            project_name=project.name,
            version=profile.version,
            mode=profile.mode,
            adapter_id=profile.adapter_id,
            adapter_version=profile.adapter_version,
            scoring_rule_version=profile.scoring_rule_version,
            required_fields=list(profile.required_fields),
            confidence_thresholds=dict(profile.confidence_thresholds),
            auto_publish_warning_levels=list(profile.auto_publish_warning_levels),
            publish_mode=profile.publish_mode,
            effective_from=profile.effective_from,
            configured_by_name=configured_by_name,
            change_reason=profile.change_reason,
            is_current=current is not None and current.id == profile.id,
        )
        for profile, project, configured_by_name in rows
    ]


@router.get(
    "/projects/{project_id}/analysis-profiles",
    response_model=list[AnalysisProfileItem],
)
def list_analysis_profiles(
    project_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[AnalysisProfileService, Depends(get_analysis_profile_service)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AnalysisProfileItem]:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_analysis_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    _require_authorized_analysis_project(
        db,
        account_id=context.account.id,
        project_id=project_id,
        occurred_at=occurred_at,
    )
    return _analysis_profile_items(
        db,
        service,
        project_id=project_id,
        occurred_at=occurred_at,
    )


@router.post(
    "/projects/{project_id}/analysis-profiles",
    response_model=AnalysisProfileItem,
    status_code=201,
)
def create_analysis_profile(
    project_id: UUID,
    input: AnalysisProfileCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[AnalysisProfileService, Depends(get_analysis_profile_service)],
    db: Annotated[Session, Depends(get_db)],
) -> AnalysisProfileItem:
    require_evaluation_center(context=context, service=context_service)
    occurred_at = datetime.now(UTC)
    _require_analysis_foundation_read(
        db,
        context_service=context_service,
        account_id=context.account.id,
        occurred_at=occurred_at,
    )
    _require_authorized_analysis_project(
        db,
        account_id=context.account.id,
        project_id=project_id,
        occurred_at=occurred_at,
    )
    try:
        profile = service.create_version(
            project_id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except (AnalysisProfileError, EvaluationResourceNotFound) as exc:
        _raise_analysis_error(exc)
    for item in _analysis_profile_items(
        db,
        service,
        project_id=project_id,
        occurred_at=occurred_at,
    ):
        if item.id == profile.id:
            return item
    raise ProblemError(
        status=500,
        code="analysis_profile_error",
        detail="分析配置暂时不可用",
    )


@router.get("/media-storage/status", response_model=MediaStorageStatusResponse)
def get_media_storage_status(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[
        EvaluationContextService,
        Depends(get_evaluation_context_service),
    ],
    service: Annotated[
        EvaluationMediaService,
        Depends(get_evaluation_media_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> MediaStorageStatusResponse:
    require_evaluation_center(context=context, service=context_service)
    _require_operation(
        db,
        account_id=context.account.id,
        permission="evaluation_media.upload",
        occurred_at=datetime.now(UTC),
    )
    status = service.storage_status()
    return MediaStorageStatusResponse(
        ready=status.ready,
        media_writable=status.media_writable,
        quarantine_writable=status.quarantine_writable,
        max_bytes=status.max_bytes,
        max_pixels=status.max_pixels,
        preview_max_edge=status.preview_max_edge,
    )
