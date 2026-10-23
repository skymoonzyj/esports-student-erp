from __future__ import annotations

import base64
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_today
from app.identity.document_identity import DocumentType
from app.identity.models import GuardianRelationship, Person
from app.identity.normalization import IdentityValidationError
from app.identity.readiness import AgeStatus, GuardianStatus
from app.identity.schemas import (
    GuardianCreate,
    GuardianEndRequest,
    GuardianIssueSummary,
    GuardianRelationshipStatus,
    GuardianSummary,
    IntakeMode,
    PersonCounts,
    PersonDetail,
    PersonRole,
    PersonSummary,
    PersonType,
    PersonUpdate,
    ReadinessResponse,
    SelfProfile,
    SelfProfileUpdate,
    StaffCreate,
    StaffPersonProfile,
    StaffType,
    StudentCreate,
    VisitorCreate,
)
from app.identity.service import (
    GuardianCycle,
    GuardianRelationshipAlreadyEnded,
    GuardianRelationshipAlreadyExists,
    GuardianSelfRelationship,
    IdentityAlreadyExists,
    IdentityService,
    IdentityServiceError,
    PersonNotFound,
    StudentNumberAlreadyExists,
)
from app.rbac.service import RbacService
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest
from app.team_management.errors import TeamServiceError
from app.team_management.schemas import PersonTeamProjection, TeamAllocationProjection


class PrivateIdentityRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            if not request.url.path.endswith("/sensitive-reveal"):
                response = await original(request)
                response.headers["Cache-Control"] = "no-store, private"
                return response
            try:
                response = await original(request)
            except ProblemError as exc:
                return _sensitive_problem(
                    request,
                    status=exc.status,
                    code=exc.code,
                    detail=exc.detail,
                    errors=exc.errors,
                )
            except RequestValidationError as exc:
                return _sensitive_problem(
                    request,
                    status=422,
                    code="validation_error",
                    detail="Request validation failed",
                    errors=[
                        {
                            "field": ".".join(str(part) for part in error["loc"]),
                            "message": str(error["msg"]),
                        }
                        for error in exc.errors()
                    ],
                )
            except Exception:  # noqa: BLE001 - secure route must cover every 500 response
                return _sensitive_problem(
                    request,
                    status=500,
                    code="internal_error",
                    detail="An unexpected error occurred",
                )
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return route_handler


router = APIRouter(tags=["people"], route_class=PrivateIdentityRoute)


class PersonResponse(BaseModel):
    id: UUID
    full_name: str
    nickname: str | None
    person_types: list[PersonType]
    assisted_kind: Literal["student", "player", "both", "staff"] | None = None
    account_status: Literal["active", "disabled", "deleted", "not_activated"] = "not_activated"
    identity_number: str
    phone: str | None
    email: EmailStr | None
    sex: Literal["male", "female"] | None
    date_of_birth: date | None
    document_type: DocumentType = "cn_id"
    document_issuing_country: str | None = None
    intake_mode: IntakeMode = "strict"
    team_allocation: TeamAllocationProjection = Field(
        default_factory=lambda: TeamAllocationProjection(
            visibility="denied", status=None, version=None
        )
    )
    staff_type: StaffType | None = None
    created_at: datetime
    age_status: AgeStatus
    guardian_status: GuardianStatus
    roles: list[PersonRole] = Field(default_factory=list)
    team: PersonTeamProjection = Field(
        default_factory=lambda: PersonTeamProjection(visibility="denied", current=None)
    )


class PersonDetailResponse(PersonResponse):
    student_number: str | None = None
    student_status: Literal["draft", "active", "inactive"] | None = None
    staff_type: (
        Literal["coach", "life_teacher", "customer_service", "administration", "other"] | None
    ) = None
    visit_reason: str | None = None
    linked_student_id: UUID | None = None
    account_management_allowed: bool = False


class PersonPage(BaseModel):
    items: list[PersonResponse]
    page: int
    page_size: int
    total: int


class GuardianResponse(PersonResponse):
    relationship_id: UUID
    relationship: str
    is_primary: bool
    valid_from: date
    valid_until: date | None
    status: GuardianRelationshipStatus
    ended_at: datetime | None
    end_reason: str | None


class GuardianLinkRequest(BaseModel):
    guardian_id: UUID
    relationship: str = Field(min_length=2, max_length=40)
    is_primary: bool = False
    valid_from: date
    valid_until: date | None = None


class SensitiveRevealRequest(BaseModel):
    field: Literal["phone", "identity_number"]
    purpose: str = Field(min_length=4, max_length=200)


class SensitiveRevealResponse(BaseModel):
    value: str


def _decode_secret(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


def get_identity_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> IdentityService:
    settings = request.app.state.settings
    return IdentityService(
        db,
        cipher=EnvelopeCipher(
            _decode_secret(settings.field_encryption_master_key.get_secret_value())
        ),
        lookup=LookupDigest(_decode_secret(settings.field_lookup_key.get_secret_value())),
    )


def _summary_response(value: PersonSummary) -> PersonResponse:
    data = value.model_dump()
    data["identity_number"] = data.pop("masked_identity")
    data["phone"] = data.pop("masked_phone")
    return PersonResponse(**data)


def _detail_response(
    value: PersonDetail,
    *,
    account_management_allowed: bool = False,
) -> PersonDetailResponse:
    data = value.model_dump()
    data["identity_number"] = data.pop("masked_identity")
    data["phone"] = data.pop("masked_phone")
    return PersonDetailResponse(
        **data,
        account_management_allowed=account_management_allowed,
    )


def _guardian_response(value: GuardianSummary) -> GuardianResponse:
    data = value.model_dump()
    data["identity_number"] = data.pop("masked_identity")
    data["phone"] = data.pop("masked_phone")
    return GuardianResponse(**data)


def _family_scope(db: Session, guardian_id: UUID, student_id: UUID) -> bool:
    today = business_today()
    return (
        db.scalar(
            select(GuardianRelationship.guardian_person_id).where(
                GuardianRelationship.guardian_person_id == guardian_id,
                GuardianRelationship.student_person_id == student_id,
                GuardianRelationship.requires_verification.is_(False),
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.valid_from <= today,
                (
                    GuardianRelationship.valid_until.is_(None)
                    | (GuardianRelationship.valid_until >= today)
                ),
            )
        )
        is not None
    )


def _person_tokens(context: AuthContext, db: Session, person_id: UUID) -> set[str]:
    tokens = {"global", f"person:{person_id}"}
    if context.person.id == person_id:
        tokens.add(f"self:{person_id}")
    if _family_scope(db, context.person.id, person_id):
        tokens.add(f"family:{person_id}")
    return tokens


def _require_permission(
    context: AuthContext,
    db: Session,
    permission: str,
    resource_tokens: set[str],
) -> None:
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=permission,
        resource_tokens=resource_tokens,
    )
    if not decision.allowed:
        raise ProblemError(
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )


def _raise_identity_error(exc: ValueError) -> NoReturn:
    if isinstance(exc, TeamServiceError):
        raise ProblemError(status=exc.status, code=exc.code, detail=exc.detail) from exc
    if isinstance(exc, IdentityValidationError):
        raise ProblemError(
            status=422,
            code=exc.code,
            detail=str(exc),
            errors=[{"field": exc.field, "message": str(exc)}],
        ) from exc
    if isinstance(exc, PersonNotFound):
        raise ProblemError(status=404, code=exc.code, detail="人员不存在") from exc
    if isinstance(exc, GuardianRelationshipAlreadyEnded):
        raise ProblemError(status=409, code=exc.code, detail="监护关系已经结束") from exc
    if isinstance(
        exc,
        (
            IdentityAlreadyExists,
            StudentNumberAlreadyExists,
            GuardianRelationshipAlreadyExists,
        ),
    ):
        raise ProblemError(status=409, code=exc.code, detail="人员资料已存在") from exc
    if isinstance(exc, (GuardianSelfRelationship, GuardianCycle)):
        raise ProblemError(status=422, code=exc.code, detail="监护关系无效") from exc
    code = exc.code if isinstance(exc, IdentityServiceError) else "invalid_request"
    raise ProblemError(status=422, code=code, detail="人员资料无效") from exc


@router.post("/people/students", response_model=PersonResponse, status_code=201)
def create_student(
    input: StudentCreate,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonResponse:
    _require_permission(context, db, "people.create", {"global"})
    try:
        return _summary_response(service.create_student(input))
    except ValueError as exc:
        _raise_identity_error(exc)


@router.post("/people/guardians", response_model=PersonResponse, status_code=201)
def create_guardian(
    input: GuardianCreate,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonResponse:
    _require_permission(context, db, "people.create", {"global"})
    try:
        return _summary_response(service.create_guardian(input))
    except ValueError as exc:
        _raise_identity_error(exc)


@router.post("/people/staff", response_model=PersonResponse, status_code=201)
def create_staff(
    input: StaffCreate,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonResponse:
    _require_permission(context, db, "people.create", {"global"})
    try:
        return _summary_response(service.create_staff(input))
    except ValueError as exc:
        _raise_identity_error(exc)


@router.post("/people/visitors", response_model=PersonResponse, status_code=201)
def create_visitor(
    input: VisitorCreate,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonResponse:
    _require_permission(context, db, "people.create", {"global"})
    try:
        return _summary_response(service.create_visitor(input))
    except ValueError as exc:
        _raise_identity_error(exc)


@router.get("/people", response_model=PersonPage)
def list_people(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
    phone: str | None = None,
    name: Annotated[str | None, Query(max_length=80)] = None,
    type: PersonType | None = None,
    staff_type: StaffType | None = None,
    role: Literal["team_member", "team_manager"] | None = None,
    team_allocation_status: Literal["pending", "assigned"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PersonPage:
    _require_permission(context, db, "people.list", {"global"})
    try:
        result = service.search_people(
            phone=phone,
            name=name,
            person_type=type,
            staff_type=staff_type,
            role=role,
            team_allocation_status=team_allocation_status,
            actor=context,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        _raise_identity_error(exc)
    return PersonPage(
        items=[_summary_response(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/people/counts", response_model=PersonCounts)
def count_people(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonCounts:
    _require_permission(context, db, "people.list", {"global"})
    return service.count_people(actor=context)


@router.get("/people/me/profile", response_model=SelfProfile)
def get_self_profile(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> SelfProfile:
    try:
        return service.get_self_profile(context.person.id)
    except ValueError as exc:
        _raise_identity_error(exc)


@router.patch("/people/me/profile", response_model=SelfProfile)
def update_self_profile(
    input: SelfProfileUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> SelfProfile:
    try:
        return service.update_self_profile(
            context.person.id,
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="profile_audit_failed",
            detail="个人资料审计记录失败",
        ) from exc
    except ValueError as exc:
        _raise_identity_error(exc)


@router.get("/people/{person_id}/staff-profile", response_model=StaffPersonProfile)
def get_staff_profile(
    person_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> StaffPersonProfile:
    tokens = _person_tokens(context, db, person_id)
    _require_permission(context, db, "people.update", {"global"})
    _require_permission(context, db, "people.update", tokens)
    _require_permission(context, db, "people.read", tokens)
    try:
        return service.get_staff_profile(person_id)
    except ValueError as exc:
        _raise_identity_error(exc)


@router.get("/people/{person_id}", response_model=PersonDetailResponse)
def get_person(
    person_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonDetailResponse:
    tokens = _person_tokens(context, db, person_id)
    _require_permission(context, db, "people.read", tokens)
    try:
        account_management_allowed = (
            RbacService(db)
            .decide(
                user_id=context.account.id,
                permission="accounts.manage",
                resource_tokens=tokens,
            )
            .allowed
        )
        return _detail_response(
            service.get_person(person_id, actor=context),
            account_management_allowed=account_management_allowed,
        )
    except ValueError as exc:
        _raise_identity_error(exc)


@router.patch("/people/{person_id}", response_model=PersonDetailResponse)
def update_person(
    person_id: UUID,
    input: PersonUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> PersonDetailResponse:
    tokens = _person_tokens(context, db, person_id)
    _require_permission(context, db, "people.update", tokens)
    if input.model_fields_set & {"nickname", "profile_notes"}:
        _require_permission(context, db, "people.update", {"global"})
        _require_permission(context, db, "people.read", tokens)
    try:
        account_management_allowed = (
            RbacService(db)
            .decide(
                user_id=context.account.id,
                permission="accounts.manage",
                resource_tokens=tokens,
            )
            .allowed
        )
        return _detail_response(
            service.update_person(
                person_id,
                input,
                actor_user_id=context.account.id,
                request_id=str(request.state.request_id),
            ),
            account_management_allowed=account_management_allowed,
        )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="guardian_audit_failed",
            detail="监护关系审计记录失败",
        ) from exc
    except ValueError as exc:
        _raise_identity_error(exc)


@router.post(
    "/people/{student_id}/guardians",
    response_model=GuardianResponse,
    status_code=201,
)
def add_guardian(
    student_id: UUID,
    input: GuardianLinkRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> GuardianResponse:
    _require_permission(context, db, "people.update", _person_tokens(context, db, student_id))
    _require_permission(
        context,
        db,
        "people.update",
        _person_tokens(context, db, input.guardian_id),
    )
    try:
        db.commit()
        with db.begin():
            try:
                result = service.add_guardian(
                    student_id,
                    input.guardian_id,
                    relationship=input.relationship,
                    is_primary=input.is_primary,
                    valid_from=input.valid_from,
                    valid_until=input.valid_until,
                )
            except ValueError as exc:
                _raise_identity_error(exc)
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="guardian.relationship.create",
                resource_type="guardian_relationship",
                resource_id=result.relationship_id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={
                    "student_person_id": str(student_id),
                    "guardian_person_id": str(input.guardian_id),
                },
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="guardian_audit_failed",
            detail="监护关系审计记录失败",
        ) from exc
    return _guardian_response(result)


@router.get("/people/{student_id}/guardians", response_model=list[GuardianResponse])
def list_guardians(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
    include_inactive: bool = False,
) -> list[GuardianResponse]:
    _require_permission(context, db, "people.read", _person_tokens(context, db, student_id))
    if include_inactive:
        _require_permission(
            context,
            db,
            "people.update",
            _person_tokens(context, db, student_id),
        )
    reference_date = None if include_inactive else business_today()
    try:
        guardian_ids = service.list_guardian_person_ids(student_id, reference_date)
    except ValueError as exc:
        _raise_identity_error(exc)
    for guardian_id in guardian_ids:
        _require_permission(
            context,
            db,
            "people.read",
            _person_tokens(context, db, guardian_id),
        )
    try:
        guardians = service.list_guardians(
            student_id,
            reference_date=reference_date,
            authorized_guardian_ids=set(guardian_ids),
        )
        return [_guardian_response(item) for item in guardians]
    except ValueError as exc:
        _raise_identity_error(exc)


@router.post(
    "/people/{student_id}/guardians/{relationship_id}/end",
    response_model=GuardianResponse,
)
def end_guardian_relationship(
    student_id: UUID,
    relationship_id: UUID,
    input: GuardianEndRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> GuardianResponse:
    _require_permission(context, db, "people.update", _person_tokens(context, db, student_id))
    try:
        db.commit()
        with db.begin():
            try:
                result = service.end_guardian_relationship(
                    student_id,
                    relationship_id,
                    ended_by_user_id=context.account.id,
                    reason=input.reason,
                    ended_at=datetime.now(UTC),
                    request_id=str(request.state.request_id),
                    include_email=False,
                )
                if (
                    RbacService(db)
                    .decide(
                        user_id=context.account.id,
                        permission="people.read",
                        resource_tokens=_person_tokens(context, db, result.id),
                    )
                    .allowed
                ):
                    result = result.model_copy(
                        update={
                            "email": service.decrypt_email_for_authorized_person(result.id),
                        }
                    )
            except AuditMetadataRejected:
                raise
            except ValueError as exc:
                _raise_identity_error(exc)
            AuditService(db).append(
                actor_user_id=context.account.id,
                action="guardian.relationship.end",
                resource_type="guardian_relationship",
                resource_id=relationship_id,
                purpose=None,
                outcome="allowed",
                request_id=str(request.state.request_id),
                metadata={"student_person_id": str(student_id)},
            )
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=500,
            code="guardian_audit_failed",
            detail="监护关系审计记录失败",
        ) from exc
    return _guardian_response(result)


@router.delete(
    "/people/{student_id}/guardians/{guardian_id}",
    status_code=405,
    include_in_schema=False,
)
def reject_guardian_deletion(student_id: UUID, guardian_id: UUID) -> Response:
    return Response(status_code=405)


@router.get(
    "/people/{person_id}/readiness",
    response_model=ReadinessResponse,
    response_model_exclude_unset=True,
)
def readiness(
    person_id: UUID,
    reference_date: date,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> ReadinessResponse:
    _require_permission(context, db, "people.read", _person_tokens(context, db, person_id))
    include_guardian_issues = any(
        RbacService(db).account_has_role(context.account.id, role_key)
        for role_key in ("super_admin", "admin")
    )
    try:
        result = service.readiness(
            person_id,
            reference_date=reference_date,
            include_guardian_issues=include_guardian_issues,
        )
    except ValueError as exc:
        _raise_identity_error(exc)
    if not include_guardian_issues:
        return ReadinessResponse(
            ready=result.ready,
            missing=result.missing,
            age_status=result.age_status,
            guardian_status=result.guardian_status,
        )
    return ReadinessResponse(
        ready=result.ready,
        missing=result.missing,
        age_status=result.age_status,
        guardian_status=result.guardian_status,
        guardian_issues=[
            GuardianIssueSummary(
                relationship_id=issue.relationship_id,
                guardian_person_id=issue.guardian_person_id,
                account_id=issue.account_id,
                reason_codes=list(issue.reason_codes),
            )
            for issue in result.guardian_issues
        ],
    )


def _sensitive_problem(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": str(request.state.request_id),
    }
    if errors is not None:
        content["errors"] = errors
    response = JSONResponse(
        status_code=status,
        content=content,
    )
    response.headers["Cache-Control"] = "no-store, private"
    return response


@router.post(
    "/people/{person_id}/sensitive-reveal",
    response_model=SensitiveRevealResponse,
    responses={403: {"model": None}, 500: {"model": None}},
)
def reveal_sensitive_field(
    person_id: UUID,
    input: SensitiveRevealRequest,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> SensitiveRevealResponse | JSONResponse:
    try:
        AuditService.validate_purpose(input.purpose)
    except AuditMetadataRejected as exc:
        raise ProblemError(
            status=422,
            code="invalid_purpose",
            detail="查看用途不能包含完整手机号或身份证",
            errors=[{"field": "purpose", "message": "请填写不含敏感信息的业务用途"}],
        ) from exc
    action = "person.phone.reveal" if input.field == "phone" else "person.identity.reveal"
    tokens = _person_tokens(context, db, person_id)
    decision = RbacService(db).decide(
        user_id=context.account.id,
        permission=f"people.{input.field.replace('_number', '')}.reveal",
        resource_tokens=tokens,
    )
    audit = AuditService(db)
    if not decision.allowed:
        audit.append(
            actor_user_id=context.account.id,
            action=action,
            resource_type="person",
            resource_id=person_id,
            purpose=input.purpose,
            outcome="denied",
            request_id=str(request.state.request_id),
            metadata={"field": input.field},
        )
        db.commit()
        return _sensitive_problem(
            request,
            status=403,
            code="permission_denied",
            detail="没有执行此操作的权限",
        )

    person = db.scalar(select(Person).where(Person.id == person_id, Person.deleted_at.is_(None)))
    try:
        if person is None:
            raise EncryptedFieldError("sensitive value unavailable")
        ciphertext = (
            person.phone_ciphertext if input.field == "phone" else person.identity_ciphertext
        )
        if ciphertext is None:
            raise EncryptedFieldError("sensitive value unavailable")
        value = service.cipher.decrypt(ciphertext, field_name=input.field)
    except EncryptedFieldError:
        audit.append(
            actor_user_id=context.account.id,
            action=action,
            resource_type="person",
            resource_id=person_id,
            purpose=input.purpose,
            outcome="failed",
            request_id=str(request.state.request_id),
            metadata={"field": input.field, "error_code": "decrypt_failed"},
        )
        db.commit()
        return _sensitive_problem(
            request,
            status=500,
            code="sensitive_reveal_failed",
            detail="敏感资料暂时无法查看",
        )

    audit.append(
        actor_user_id=context.account.id,
        action=action,
        resource_type="person",
        resource_id=person_id,
        purpose=input.purpose,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={"field": input.field},
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store, private"
    return SensitiveRevealResponse(value=value)
