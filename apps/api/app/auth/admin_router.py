from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import get_password_service, require_standard_account
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate, AccountStatus
from app.auth.service import (
    AccountContactMismatch,
    AccountService,
    AccountServiceError,
    AuthContext,
    InvalidAccountContact,
    LastActiveAdminProtected,
    PersonNotReady,
)
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_today
from app.identity.router import get_identity_service
from app.identity.schemas import PersonType
from app.rbac.service import RbacService

router = APIRouter(tags=["accounts"])


class AccountResponse(BaseModel):
    id: UUID
    person_id: UUID
    full_name: str
    phone: str
    email: EmailStr
    status: AccountStatus
    force_password_change: bool
    created_at: datetime


class AccountPage(BaseModel):
    items: list[AccountResponse]
    page: int
    page_size: int
    total: int


class AccountCreatedResponse(BaseModel):
    account: AccountResponse
    temporary_password: str


class PasswordResetResponse(BaseModel):
    account_id: UUID
    temporary_password: str


class AccountCandidateResponse(BaseModel):
    id: UUID
    full_name: str
    person_types: list[PersonType]
    phone: str


def get_account_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AccountService:
    return AccountService(
        db,
        identity_service=get_identity_service(request, db),
        passwords=get_password_service(),
        reference_date_provider=business_today,
    )


def _account_response(value: object) -> AccountResponse:
    data = value.model_dump()  # type: ignore[attr-defined]
    data["phone"] = data.pop("masked_login_phone")
    return AccountResponse(**data)


def _tokens(context: AuthContext, db: Session, account_id: UUID | None = None) -> set[str]:
    tokens = {"global"}
    if account_id is None:
        return tokens
    account = db.get(UserAccount, account_id)
    if account is not None:
        tokens = RbacService(db).person_resource_tokens(
            actor_person_id=context.person.id,
            target_person_id=account.person_id,
        )
    return tokens


def _require_manage(context: AuthContext, db: Session, tokens: set[str]) -> None:
    if not RbacService(db).decide(
        user_id=context.account.id,
        permission="accounts.manage",
        resource_tokens=tokens,
    ).allowed:
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")


def _require_manage_available(context: AuthContext, db: Session) -> None:
    if "accounts.manage" not in RbacService(db).available_permissions(
        user_id=context.account.id
    ):
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")


def _raise_account_error(exc: AccountServiceError) -> None:
    if isinstance(exc, LastActiveAdminProtected):
        raise ProblemError(status=409, code=exc.code, detail="不能停用最后一个管理员账号") from exc
    if isinstance(exc, PersonNotReady):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="人员资料尚未满足账号开通条件",
            errors=[{"field": item, "message": "required"} for item in exc.missing],
        ) from exc
    if isinstance(exc, InvalidAccountContact):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail=str(exc),
            errors=[{"field": exc.field, "message": str(exc)}],
        ) from exc
    if isinstance(exc, AccountContactMismatch):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail=str(exc),
            errors=[{"field": exc.field, "message": str(exc)}],
        ) from exc
    status = 404 if exc.code in {"account_not_found", "person_not_found"} else 409
    raise ProblemError(status=status, code=exc.code, detail="账号操作无法完成") from exc


def _append_account_audit(
    *,
    db: Session,
    request: Request,
    actor_user_id: UUID,
    action: str,
    account_id: UUID,
) -> None:
    AuditService(db).append(
        actor_user_id=actor_user_id,
        action=action,
        resource_type="account",
        resource_id=account_id,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={},
    )


def _append_guardian_missing_audit(
    *,
    db: Session,
    request: Request,
    actor_user_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID,
) -> None:
    with db.begin():
        AuditService(db).append(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=None,
            outcome="denied",
            request_id=str(request.state.request_id),
            metadata={"error_code": "minor_guardian_required"},
        )


@router.post("/accounts", response_model=AccountCreatedResponse, status_code=201)
def create_account(
    input: AccountCreate,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> AccountCreatedResponse:
    tokens = RbacService(db).person_resource_tokens(
        actor_person_id=context.person.id,
        target_person_id=input.person_id,
    )
    _require_manage(context, db, tokens)
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            created = service.create_account(input)
            _append_account_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.create",
                account_id=created.account.id,
            )
    except PersonNotReady as exc:
        if "guardian" in exc.missing:
            _append_guardian_missing_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.create.guardian_missing",
                resource_type="person",
                resource_id=input.person_id,
            )
        _raise_account_error(exc)
    except AccountServiceError as exc:
        _raise_account_error(exc)
    response.headers["Cache-Control"] = "no-store, private"
    return AccountCreatedResponse(
        account=_account_response(created.account),
        temporary_password=created.temporary_password,
    )


@router.get("/accounts", response_model=AccountPage)
def list_accounts(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[AccountService, Depends(get_account_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "disabled"] | None = None,
) -> AccountPage:
    _require_manage(context, db, {"global"})
    result = service.list_accounts(
        page=page,
        page_size=page_size,
        status=status,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return AccountPage(
        items=[_account_response(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/account-candidates", response_model=list[AccountCandidateResponse])
def list_account_candidates(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AccountService, Depends(get_account_service)],
    name: Annotated[str, Query(min_length=1, max_length=80)],
) -> list[AccountCandidateResponse]:
    _require_manage_available(context, db)
    rbac = RbacService(db)
    candidates = service.identity_service.search_account_candidates(name=name.strip())
    response.headers["Cache-Control"] = "no-store, private"
    return [
        AccountCandidateResponse(
            id=candidate.id,
            full_name=candidate.full_name,
            person_types=candidate.person_types,
            phone=candidate.masked_phone,
        )
        for candidate in candidates
        if rbac.decide(
            user_id=context.account.id,
            permission="accounts.manage",
            resource_tokens=rbac.person_resource_tokens(
                actor_person_id=context.person.id,
                target_person_id=candidate.id,
            ),
        ).allowed
    ]


@router.post("/accounts/{account_id}/disable", status_code=204)
def disable_account(
    account_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> Response:
    _require_manage(context, db, _tokens(context, db, account_id))
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            service.disable_account(
                account_id,
                actor_user_id=actor_user_id,
                request_id=str(request.state.request_id),
            )
            _append_account_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.disable",
                account_id=account_id,
            )
    except AccountServiceError as exc:
        _raise_account_error(exc)
    return Response(status_code=204)


@router.post("/accounts/{account_id}/enable", status_code=204)
def enable_account(
    account_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> Response:
    _require_manage(context, db, _tokens(context, db, account_id))
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            service.enable_account(account_id)
            _append_account_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.enable",
                account_id=account_id,
            )
    except PersonNotReady as exc:
        if "guardian" in exc.missing:
            _append_guardian_missing_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.enable.guardian_missing",
                resource_type="account",
                resource_id=account_id,
            )
        _raise_account_error(exc)
    except AccountServiceError as exc:
        _raise_account_error(exc)
    return Response(status_code=204)


@router.post("/accounts/{account_id}/reset-password", response_model=PasswordResetResponse)
def reset_password(
    account_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> PasswordResetResponse:
    _require_manage(context, db, _tokens(context, db, account_id))
    actor_user_id = context.account.id
    try:
        db.commit()
        with db.begin():
            result = service.reset_password(account_id)
            _append_account_audit(
                db=db,
                request=request,
                actor_user_id=actor_user_id,
                action="account.password.reset",
                account_id=account_id,
            )
    except AccountServiceError as exc:
        _raise_account_error(exc)
    response.headers["Cache-Control"] = "no-store, private"
    return PasswordResetResponse(**result.model_dump())
